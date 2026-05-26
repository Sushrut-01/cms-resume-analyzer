from pydantic import BaseModel


class ClientRequirementOut(BaseModel):
    id: int
    client_name: str
    job_title: str
    jd_text: str
    active: bool

    class Config:
        from_attributes = True


class ClientRequirementCreate(BaseModel):
    client_name: str
    job_title: str
    jd_text: str