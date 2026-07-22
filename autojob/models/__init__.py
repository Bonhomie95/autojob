"""
SQLAlchemy models for AutoJob SaaS.

Importing this package registers every model on ``db.metadata`` so that
Alembic autogeneration and ``db.create_all()`` see the full schema. Import
models from here, not from the submodules, e.g.::

    from autojob.models import User, Job
"""

from .corpus import CorpusMeta, TokenDf
from .cv import CvChoice, CvDocument, CvProfile
from .job import Job, Run
from .user import User, UserCredential, UserSettings

__all__ = [
    "User",
    "UserSettings",
    "UserCredential",
    "CvDocument",
    "CvProfile",
    "CvChoice",
    "Job",
    "Run",
    "TokenDf",
    "CorpusMeta",
]
