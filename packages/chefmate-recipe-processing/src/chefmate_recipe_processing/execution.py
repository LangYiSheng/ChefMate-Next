"""One processing workflow with synchronous and asynchronous operation runners."""
import asyncio
import copy
from dataclasses import dataclass, field
from weakref import WeakKeyDictionary, ref

from .protocols import RequestContext

_io_limits = WeakKeyDictionary()


async def run_io(function, *args, **kwargs):
    loop = asyncio.get_running_loop()
    reference = _io_limits.get(loop)
    limit = reference() if reference is not None else None
    if limit is None:
        limit = asyncio.Semaphore(8)
        _io_limits[loop] = ref(limit)
    # Keep the permit until the worker actually exits, even if its caller cancels.
    await limit.acquire()
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    def completed(worker):
        limit.release()
        if not worker.cancelled():
            worker.exception()  # Retrieve errors even when the waiting caller was cancelled.
    task.add_done_callback(completed)
    return await asyncio.shield(task)


@dataclass
class Operation:
    function: object
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    async_function: object = None
    is_model: bool = False


def io(function, *args, **kwargs):
    return Operation(function, args, kwargs)


def model_call(model, *args, **kwargs):
    return Operation(getattr(model, "request", None), args, kwargs, getattr(model, "arequest", None), True)


def run(workflow):
    try:
        operation = next(workflow)
        while True:
            try:
                if operation.function is None:
                    raise TypeError("同步处理需要提供 request() 的模型适配器")
                result = operation.function(*operation.args, **operation.kwargs)
            except Exception as exc:
                operation = workflow.throw(exc)
            else:
                operation = workflow.send(result)
    except StopIteration as done:
        return done.value
    finally:
        workflow.close()


async def arun(workflow):
    try:
        operation = next(workflow)
        while True:
            try:
                if operation.async_function is not None:
                    result = await operation.async_function(*operation.args, **operation.kwargs)
                elif operation.is_model:
                    raise TypeError("异步处理需要提供 arequest() 的模型适配器")
                else:
                    result = await run_io(operation.function, *operation.args, **operation.kwargs)
            except Exception as exc:
                operation = workflow.throw(exc)
            else:
                operation = workflow.send(result)
    except StopIteration as done:
        return done.value
    finally:
        workflow.close()


def bind_model(model, *, context=None, progress=None):
    if hasattr(model, "bind"):
        return model.bind(context=context, progress=progress)
    # Legacy injected adapters get a shallow invocation copy; their observations
    # (e.g. a test call list) remain shared, but configuration/callbacks do not.
    bound = copy.copy(model)
    if hasattr(model, "settings"):
        bound.settings = {**model.settings, **(context.settings if context else {})}
    if progress is not None:
        bound.progress = progress
    if context is not None:
        for name in ("progress", "on_call", "on_error"):
            value = getattr(context, name)
            if value is not None:
                setattr(bound, name, value)
    return bound


def invocation_context(settings, context=None, progress=None):
    context = context or RequestContext()
    return RequestContext(settings={**settings, **context.settings},
                          progress=progress if progress is not None else context.progress,
                          on_call=context.on_call, on_error=context.on_error)
