from .text_utils import clean_text
from ..database import derive_workspace_id

def workspace_id_for_user(user: dict) -> str:
    if not isinstance(user, dict):
        return derive_workspace_id()
    explicit = clean_text(user.get("workspace_id"))
    if explicit:
        return explicit
    return derive_workspace_id(
        clean_text(user.get("account_name")),
        clean_text(user.get("username")),
        int(user.get("id") or 0),
    )
