"""Response schemas shared by several routers."""

from pydantic import BaseModel


class OkOut(BaseModel):
    ok: bool = True
