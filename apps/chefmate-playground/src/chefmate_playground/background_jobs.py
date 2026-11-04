"""Workspace-owned jobs: HTTP clients observe, never own, execution."""
import asyncio
import json
from uuid import uuid4

from .sessions import now


class BackgroundJobs:
    def __init__(self, database):
        self.database = database
        self.active = {}
        self.lock = asyncio.Lock()
        self.slots = asyncio.Semaphore(2)
        with database.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS background_jobs (key TEXT PRIMARY KEY, body TEXT NOT NULL)")
            for row in db.execute("SELECT key,body FROM background_jobs"):
                job = json.loads(row[1])
                if job['status'] in {'queued', 'running'}:
                    job.update(status='interrupted', error='服务重启，点击继续生成即可。', updated_at=now())
                    db.execute("UPDATE background_jobs SET body=? WHERE key=?", (json.dumps(job), row[0]))

    def get(self, key):
        with self.database.connect() as db:
            row = db.execute("SELECT body FROM background_jobs WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def save(self, key, job):
        with self.database.connect() as db:
            db.execute("INSERT INTO background_jobs(key,body) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET body=excluded.body",
                       (key, json.dumps(job, ensure_ascii=False)))

    async def submit(self, key, revision, operation):
        async with self.lock:
            if key in self.active:
                return await asyncio.to_thread(self.get, key)
            if len(self.active) >= 8:
                from fastapi import HTTPException
                raise HTTPException(429, '后台任务较多，请稍后再试。')
            job = dict(id=str(uuid4()), revision=revision, status='queued', error=None, updated_at=now())
            await asyncio.to_thread(self.save, key, job)
            self.active[key] = asyncio.create_task(self.run(key, job, operation))
            return dict(job)

    async def run(self, key, job, operation):
        try:
            async with self.slots:
                job.update(status='running', updated_at=now())
                await asyncio.to_thread(self.save, key, job)
                await operation()
                job.update(status='completed', error=None)
        except asyncio.CancelledError:
            job.update(status='interrupted', error='生成已中断，点击继续生成即可。')
        except Exception as error:
            from fastapi import HTTPException
            job.update(status='error', error=str(error.detail) if isinstance(error, HTTPException)
                       else '这次生成未完成，可以重试。')
        finally:
            job['updated_at'] = now()
            try:
                await asyncio.to_thread(self.save, key, job)
            finally:
                self.active.pop(key, None)

    async def close(self):
        tasks = list(self.active.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
