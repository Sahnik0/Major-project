from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from apps.api.app.config import settings
from apps.api.app.db import AppSetting, get_db
from apps.api.app.schemas.settings import SettingsResponse, SettingsUpdateRequest

router = APIRouter(prefix="/settings", tags=["Settings"])


def _get_setting_or_default(db: Session, key: str, default: str) -> str:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    return row.value if row else default


@router.get("", response_model=SettingsResponse)
def get_settings(db: Session = Depends(get_db)) -> SettingsResponse:
    ollama_url = _get_setting_or_default(db, "ollama_url", settings.OLLAMA_URL)
    ollama_model = _get_setting_or_default(db, "ollama_model", settings.OLLAMA_MODEL)
    timesfm_version = _get_setting_or_default(db, "timesfm_version", settings.TIMESFM_VERSION)
    default_country = _get_setting_or_default(db, "default_country", settings.DEFAULT_COUNTRY)
    service_level = float(
        _get_setting_or_default(db, "default_service_level", str(settings.DEFAULT_SERVICE_LEVEL))
    )
    lead_time = int(
        _get_setting_or_default(db, "default_lead_time_days", str(settings.DEFAULT_LEAD_TIME_DAYS))
    )
    review_period = int(
        _get_setting_or_default(
            db, "default_review_period_days", str(settings.DEFAULT_REVIEW_PERIOD_DAYS)
        )
    )
    horizon = int(_get_setting_or_default(db, "default_horizon", str(settings.DEFAULT_HORIZON)))

    license_warning = (
        "TimesFM 3.0 pretrained weights are licensed under the 'timesfm-non-commercial-license-v1.0' "
        "for non-commercial, research, and non-production use only. For commercial deployment, "
        "switch to TimesFM 2.5 (Apache-2.0)."
        if timesfm_version == "3.0"
        else "TimesFM 2.5 is licensed under Apache-2.0 (commercial-safe)."
    )

    return SettingsResponse(
        environment=settings.ENVIRONMENT,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        timesfm_version=timesfm_version,
        timesfm_repo=settings.TIMESFM_REPO,
        timesfm_license_warning=license_warning,
        hf_token_configured=bool(settings.HF_TOKEN and settings.HF_TOKEN.strip()),
        hf_hub_offline=settings.HF_HUB_OFFLINE,
        default_country=default_country,
        default_service_level=service_level,
        default_lead_time_days=lead_time,
        default_review_period_days=review_period,
        default_horizon=horizon,
    )


@router.put("", response_model=SettingsResponse)
def update_settings(
    req: SettingsUpdateRequest,
    db: Session = Depends(get_db),
) -> SettingsResponse:
    if req.timesfm_version and req.timesfm_version not in ["3.0", "2.5"]:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_TIMESFM_VERSION",
                "message": "TimesFM version must be either '3.0' or '2.5'.",
                "hint": "Set version to '3.0' (latest) or '2.5' (commercial Apache-2.0).",
            },
        )

    updates = {
        "ollama_url": req.ollama_url,
        "ollama_model": req.ollama_model,
        "timesfm_version": req.timesfm_version,
        "default_country": req.default_country,
        "default_service_level": str(req.default_service_level)
        if req.default_service_level is not None
        else None,
        "default_lead_time_days": str(req.default_lead_time_days)
        if req.default_lead_time_days is not None
        else None,
        "default_review_period_days": str(req.default_review_period_days)
        if req.default_review_period_days is not None
        else None,
        "default_horizon": str(req.default_horizon) if req.default_horizon is not None else None,
    }

    for key, val in updates.items():
        if val is not None:
            row = db.query(AppSetting).filter(AppSetting.key == key).first()
            if not row:
                row = AppSetting(key=key, value=val)
                db.add(row)
            else:
                row.value = val
    db.commit()

    return get_settings(db)
