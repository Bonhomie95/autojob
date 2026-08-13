"""
Plain-dataclass models for AutoJob SaaS (MongoDB, no ORM).

Import models from here, not from the submodules, e.g.::

    from autojob.models import User, Job

The global IDF corpus (``token_df``/``corpus_meta``) has no model class — it's
two trivial, non-tenant-scoped collections handled directly as dicts inside
``autojob.services.repository``.
"""

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
]
