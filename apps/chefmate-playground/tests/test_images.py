import json
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from chefmate_playground.app import create_app
from chefmate_playground.config import PlaygroundConfig


class ImageAgent:
    def __init__(self):
        self.received = None

    async def run_turn(self, messages, *, image_loader, **kwargs):
        self.received = messages
        assert image_loader(messages[-1]["images"][0]).startswith("data:image/jpeg;base64,")
        return {"status":"completed", "assistant_text":"图片收到", "messages":[*messages, {"role":"assistant","content":"图片收到"}]}


def picture(size=(2800,1400), fmt="PNG"):
    image=Image.new("RGBA",size,(255,0,0,0));output=BytesIO();image.save(output,fmt);return output.getvalue()


def make_app(tmp_path, supports=True):
    agent=ImageAgent()
    return create_app(PlaygroundConfig(supports_images=supports,sessions_database=tmp_path/"sessions.db"),agent=agent),agent


def test_image_compression_native_turn_metadata_and_session_ownership(tmp_path):
    app,agent=make_app(tmp_path)
    with TestClient(app) as client:
        first=client.post("/api/sessions").json()["id"]
        second=client.post("/api/sessions").json()["id"]
        uri=f"/api/sessions/{first}"
        response=client.post(uri+"/images?name=photo.png",content=picture(),headers={"Content-Type":"image/png"})
        assert response.status_code==200
        reference=response.json()
        assert (reference["width"],reference["height"])==(1600,800)
        assert reference["byte_size"]<1_500_000 and reference["mime_type"]=="image/jpeg"
        image_id=reference["id"]
        uploaded=client.get(uri+f"/images/{image_id}")
        with Image.open(BytesIO(uploaded.content)) as decoded:
            assert decoded.format=="JPEG" and len(decoded.getexif())==0
            assert decoded.getpixel((5,5))[0]>240  # Transparent input is flattened on white.
        assert client.get(f"/api/sessions/{second}/images/{image_id}").status_code==404
        assert client.post(f"/api/sessions/{second}/turn",json={"message":"看图","images":[image_id]}).status_code==422
        result=client.post(uri+"/turn",json={"images":[image_id]})
        assert '"status": "completed"' in result.text
        assert agent.received[-1]["images"][0]==reference
        assert agent.received[-1]["content"]==""
        persisted=client.get(uri).json()
        assert "base64" not in json.dumps(persisted)
        assert persisted["messages"][0]["images"][0]==reference
        assert client.delete(uri+f"/images/{image_id}").status_code==409
        assert client.delete(uri).status_code==200
        assert not (tmp_path/"images"/first/(image_id+".jpg")).exists()


def test_disabled_capability_invalid_file_and_unsent_removal(tmp_path):
    app,_=make_app(tmp_path/"disabled",False)
    with TestClient(app) as client:
        uri="/api/sessions/"+client.post("/api/sessions").json()["id"]
        assert client.post(uri+"/images",content=picture()).status_code==422
        assert client.post(uri+"/turn",json={"images":["not-an-upload"]}).status_code==422
    app,_=make_app(tmp_path/"enabled")
    with TestClient(app) as client:
        uri="/api/sessions/"+client.post("/api/sessions").json()["id"]
        assert client.post(uri+"/images",content=b"<svg></svg>").status_code==422
        image=client.post(uri+"/images",content=picture((50,50))).json()
        assert client.delete(uri+"/images/"+image["id"]).status_code==200
        assert client.get(uri+"/images/"+image["id"]).status_code==404
        assert client.post(uri+"/turn",json={"images":[image["id"]]}).status_code==422


def test_atomic_session_delete_returns_the_latest_attachment_state(tmp_path):
    from chefmate_playground.sessions import SessionRepository
    repository=SessionRepository(tmp_path/'sessions.sqlite3')
    state=repository.create()
    repository.mutate(state['id'],lambda latest:latest.update(image_uploads=[{'id':'new-image'}]))
    removed=repository.delete(state['id'])
    assert removed['image_uploads']==[{'id':'new-image'}]
    assert repository.get(state['id']) is None
