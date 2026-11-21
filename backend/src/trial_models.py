"""Drying-time comparison trial: matched pairs, pre-work random arm draw, frozen first tests."""
from decimal import Decimal
from sqlalchemy import String,Numeric,ForeignKey,JSON,CheckConstraint,UniqueConstraint,Boolean
from sqlalchemy.orm import Mapped,mapped_column
from .database import Base

class DryingTrial(Base):
    __tablename__='drying_trials'
    __table_args__=(CheckConstraint("state IN ('open','closed')"),)
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(70),unique=True)
    state:Mapped[str]=mapped_column(String(20),default='open')
    created_by:Mapped[str]=mapped_column(String(120))
    created_at:Mapped[str]=mapped_column(String(40))
    closed_by:Mapped[str|None]=mapped_column(String(120),nullable=True)
    closed_at:Mapped[str|None]=mapped_column(String(40),nullable=True)
    freeze_snapshot:Mapped[dict|None]=mapped_column(JSON,nullable=True)

class DryingTrialPair(Base):
    __tablename__='drying_trial_pairs'
    __table_args__=(UniqueConstraint('trial_id','blend_a_id'),UniqueConstraint('trial_id','blend_b_id'),
                    CheckConstraint('blend_a_id <> blend_b_id'),
                    CheckConstraint('long_blend_id IS NULL OR short_blend_id IS NULL OR long_blend_id <> short_blend_id'))
    id:Mapped[int]=mapped_column(primary_key=True)
    trial_id:Mapped[int]=mapped_column(ForeignKey('drying_trials.id'),index=True)
    blend_a_id:Mapped[int]=mapped_column(ForeignKey('blends.id'))
    blend_b_id:Mapped[int]=mapped_column(ForeignKey('blends.id'))
    dryer_id:Mapped[int]=mapped_column(ForeignKey('dryers.id'))
    long_blend_id:Mapped[int|None]=mapped_column(ForeignKey('blends.id'),nullable=True)
    short_blend_id:Mapped[int|None]=mapped_column(ForeignKey('blends.id'),nullable=True)
    draw_token:Mapped[str|None]=mapped_column(String(64),nullable=True)
    draw_method:Mapped[str]=mapped_column(String(60),default='secure_random_draw')
    assigned_at:Mapped[str|None]=mapped_column(String(40),nullable=True)
    assigned_by:Mapped[str|None]=mapped_column(String(120),nullable=True)

class DryingTrialMember(Base):
    """One row per blend; the unique blend_id keeps every order in at most one pair."""
    __tablename__='drying_trial_members'
    id:Mapped[int]=mapped_column(primary_key=True)
    trial_id:Mapped[int]=mapped_column(ForeignKey('drying_trials.id'),index=True)
    pair_id:Mapped[int]=mapped_column(ForeignKey('drying_trial_pairs.id'),index=True)
    blend_id:Mapped[int]=mapped_column(ForeignKey('blends.id'),unique=True)
    arm:Mapped[str|None]=mapped_column(String(10),nullable=True)

class DryingTrialFirstTest(Base):
    __tablename__='drying_trial_first_tests'
    __table_args__=(UniqueConstraint('trial_id','blend_id'),CheckConstraint("arm IN ('long','short')"))
    id:Mapped[int]=mapped_column(primary_key=True)
    trial_id:Mapped[int]=mapped_column(ForeignKey('drying_trials.id'),index=True)
    pair_id:Mapped[int]=mapped_column(ForeignKey('drying_trial_pairs.id'))
    blend_id:Mapped[int]=mapped_column(ForeignKey('blends.id'))
    arm:Mapped[str]=mapped_column(String(10))
    after_moisture:Mapped[Decimal]=mapped_column(Numeric(9,4))
    moisture_limit:Mapped[Decimal]=mapped_column(Numeric(9,4))
    exceeded:Mapped[bool]=mapped_column(Boolean)
    first_record:Mapped[dict]=mapped_column(JSON)
    drying_snapshot:Mapped[dict]=mapped_column(JSON)
    registered_by:Mapped[str]=mapped_column(String(120))
    registered_at:Mapped[str]=mapped_column(String(40))
    frozen_at:Mapped[str|None]=mapped_column(String(40),nullable=True)
