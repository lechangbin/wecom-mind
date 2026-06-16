from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import UserProfile, UserProfileFact


def latest_reply_profile_payload(
    session: Session,
    userid: str | None,
) -> dict[str, Any] | None:
    if not userid:
        return None
    profiles = latest_reply_profile_payloads(session, [userid])
    return profiles.get(userid)


def latest_reply_profile_payloads(
    session: Session,
    userids: list[str] | set[str] | tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    clean_userids = []
    for userid in userids:
        if userid and userid not in clean_userids:
            clean_userids.append(str(userid))
    if not clean_userids:
        return {}

    profiles = session.scalars(
        select(UserProfile)
        .where(
            UserProfile.userid.in_(clean_userids),
            UserProfile.status == "active",
        )
        .order_by(
            UserProfile.userid.asc(),
            UserProfile.version.desc(),
            UserProfile.id.desc(),
        )
    ).all()

    latest_by_userid: dict[str, UserProfile] = {}
    for profile in profiles:
        latest_by_userid.setdefault(profile.userid, profile)

    facts_by_profile_id = _facts_by_profile_id(session, latest_by_userid.values())
    return {
        userid: _reply_profile_payload(profile, facts_by_profile_id.get(profile.id, []))
        for userid, profile in latest_by_userid.items()
    }


def _facts_by_profile_id(
    session: Session,
    profiles: Any,
) -> dict[int, list[UserProfileFact]]:
    profile_ids = [profile.id for profile in profiles]
    if not profile_ids:
        return {}

    facts = session.scalars(
        select(UserProfileFact)
        .where(
            UserProfileFact.profile_id.in_(profile_ids),
            UserProfileFact.status.in_(["active", "low_confidence"]),
        )
        .order_by(
            UserProfileFact.profile_id.asc(),
            UserProfileFact.confidence.desc(),
            UserProfileFact.id.asc(),
        )
    ).all()

    grouped: dict[int, list[UserProfileFact]] = {}
    for fact in facts:
        grouped.setdefault(fact.profile_id, []).append(fact)
    return grouped


def _reply_profile_payload(
    profile: UserProfile,
    facts: list[UserProfileFact],
) -> dict[str, Any]:
    profile_json = profile.profile_json if isinstance(profile.profile_json, dict) else {}
    fact_payloads = [_fact_payload(fact) for fact in facts]
    if not fact_payloads:
        fact_payloads = [
            item
            for item in profile_json.get("facts", [])
            if isinstance(item, dict)
        ]

    return {
        "userid": profile.userid,
        "summary": profile.summary,
        "communication_style": _first_dict(
            profile_json.get("communication_style"),
            profile_json.get("style"),
        ),
        "stable_preferences": _first_list(
            profile_json.get("stable_preferences"),
            profile_json.get("preferences"),
        ),
        "recent_focus": _first_list(
            profile_json.get("recent_focus"),
            profile_json.get("focus"),
        ),
        "facts": fact_payloads[:8],
        "confidence": float(profile.confidence),
        "version": profile.version,
        "status": profile.status,
    }


def _fact_payload(fact: UserProfileFact) -> dict[str, Any]:
    return {
        "fact_type": fact.fact_type,
        "label": fact.label,
        "description": fact.description,
        "evidence_msgids": fact.evidence_msgids,
        "evidence_conversation_nos": fact.evidence_conversation_nos,
        "confidence": float(fact.confidence),
        "status": fact.status,
    }


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _first_list(*values: Any) -> list[Any]:
    for value in values:
        if isinstance(value, list):
            return value
    return []
