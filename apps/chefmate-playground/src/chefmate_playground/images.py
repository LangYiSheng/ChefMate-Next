"""Session-owned JPEG attachments; only metadata enters durable/model histories."""

import base64
from io import BytesIO
from pathlib import Path
import warnings
from uuid import UUID, uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 4_000_000
MAX_IMAGE_BYTES = 1_500_000


class ImageError(ValueError):
    pass


class SessionImages:
    def __init__(self, root: Path, repository):
        self.root = root.resolve()
        self.repository = repository

    def path(self, session_id, image_id):
        for value in (session_id, image_id):
            if str(UUID(value)) != value:
                raise ImageError("图片标识无效。")
        path = (self.root / session_id / (image_id + ".jpg")).resolve()
        if not path.is_relative_to(self.root / session_id):
            raise ImageError("图片位置无效。")
        return path

    def save(self, session_id, data, name):
        if not data or len(data) > MAX_UPLOAD_BYTES:
            raise ImageError("图片过大，请压缩后再上传。")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as image:
                    if image.format not in {"JPEG", "PNG", "WEBP"} or image.width * image.height > 16_000_000:
                        raise ImageError("请选择尺寸合适的 JPG、PNG 或 WebP 图片。")
                    image.load()
                    image = ImageOps.exif_transpose(image)
                    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                        rgba = image.convert("RGBA")
                        flattened = Image.new("RGB", rgba.size, "white")
                        flattened.paste(rgba, mask=rgba.getchannel("A"))
                        image = flattened
                    else:
                        image = image.convert("RGB")
                    image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                    encoded = b""
                    for quality in (86, 78, 68):
                        output = BytesIO()
                        image.save(output, format="JPEG", quality=quality, optimize=True)
                        encoded = output.getvalue()
                        if len(encoded) <= MAX_IMAGE_BYTES:
                            break
                    if len(encoded) > MAX_IMAGE_BYTES:
                        raise ImageError("图片细节过多，请裁剪后再试。")
                    width, height = image.size
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ImageError("图片无法读取，请重新选择 JPG、PNG 或 WebP。") from None
        identity = str(uuid4())
        path = self.path(session_id, identity)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Encoder output contains no EXIF/location metadata from the original image.
        display_name = name.replace("\\", "/").rsplit("/", 1)[-1]
        display_name = "".join(char for char in display_name if char.isprintable())[:100] or "照片.jpg"
        reference = {"id": identity, "name": display_name, "mime_type": "image/jpeg",
                     "width": width, "height": height, "byte_size": len(encoded)}
        path.write_bytes(encoded)
        try:
            def register(state):
                uploads = state.setdefault("image_uploads", [])
                if len(uploads) >= 100:
                    raise ImageError("这个会话的图片较多，请新建会话继续。")
                uploads.append(reference)
            self.repository.mutate(session_id, register)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return reference

    def references(self, state, identities):
        uploaded = {image["id"]: image for image in state.get("image_uploads", [])}
        result = []
        for identity in identities:
            image = uploaded.get(identity)
            if image is None or not self.path(state["id"], identity).is_file():
                raise ImageError("图片不属于当前会话或已失效，请重新上传。")
            result.append(dict(image))
        return result

    def data_url(self, session_id, reference):
        state = self.repository.get(session_id)
        if state is None:
            raise ImageError("图片会话已不存在。")
        actual = self.references(state, [reference["id"]])[0]
        if actual != reference:
            raise ImageError("图片信息已变化，请重新上传。")
        data = self.path(session_id, actual["id"]).read_bytes()
        if len(data) != actual["byte_size"]:
            raise ImageError("图片已变化，请重新上传。")
        return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")

    def remove(self, session_id, identity):
        def remove(state):
            self.references(state, [identity])
            if any(image.get("id") == identity for message in state.get("messages", [])
                   for image in message.get("images", [])):
                raise ImageError("这张图片已经属于聊天记录，不能作为未发送附件移除。")
            self.path(session_id, identity).unlink(missing_ok=True)
            state["image_uploads"] = [image for image in state.get("image_uploads", []) if image["id"] != identity]
        self.repository.mutate(session_id, remove)

    def remove_session_files(self, state):
        for image in state.get("image_uploads", []):
            self.path(state["id"], image["id"]).unlink(missing_ok=True)
        directory = (self.root / state["id"]).resolve()
        if directory.is_relative_to(self.root) and directory.exists():
            try:
                directory.rmdir()
            except OSError:
                pass
