"""
IDF corpus tables — deliberately GLOBAL, not tenant-scoped.

Document-frequency counts describe the job market ("agile" is common, "wgpu"
is rare), not any one user. Sharing the corpus across all tenants makes the
scorer's IDF weighting sharper for everyone and leaks nothing personal — only
aggregate word counts over public job descriptions live here.
"""

from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


class TokenDf(db.Model):
    __tablename__ = "token_df"

    token: Mapped[str] = mapped_column(String(128), primary_key=True)
    df: Mapped[int] = mapped_column(Integer, default=0)


class CorpusMeta(db.Model):
    __tablename__ = "corpus_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), default="")
