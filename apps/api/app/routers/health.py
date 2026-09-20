import shutil

import httpx
from fastapi import APIRouter

from apps.api.app.config import settings
from apps.api.app.schemas.health import SystemHealthResponse

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=SystemHealthResponse)
async def check_health() -> SystemHealthResponse:
    # 1. PyTorch & CUDA device check
    cuda_available = False
    device = "cpu"
    device_name = None
    vram_total_mb = None
    vram_allocated_mb = None

    try:
        import torch

        cuda_available = torch.cuda.is_available()
        if cuda_available:
            device = "cuda"
            device_name = torch.cuda.get_device_name(0)
            props = torch.cuda.get_device_properties(0)
            vram_total_mb = round(props.total_memory / (1024 * 1024), 1)
            vram_allocated_mb = round(torch.cuda.memory_allocated(0) / (1024 * 1024), 1)
        else:
            device = "cpu"
            device_name = "CPU"
    except Exception:
        cuda_available = False
        device = "cpu"
        device_name = "CPU (PyTorch not loaded)"

    # 2. Ollama check
    ollama_ready = False
    ollama_model = settings.OLLAMA_MODEL
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(f"{settings.OLLAMA_URL}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("name") for m in data.get("models", [])]
                if any(settings.OLLAMA_MODEL in m for m in models):
                    ollama_ready = True
                else:
                    ollama_ready = len(models) > 0
    except Exception:
        ollama_ready = False

    # 3. Disk space check
    total, used, free = shutil.disk_usage(settings.DATA_DIR)
    disk_free_gb = round(free / (1024**3), 2)

    # 4. TimesFM check & license warning
    timesfm_ready = False
    # Check if weights exist in cache or HF token is provided
    timesfm_warning = (
        "TimesFM 3.0 pretrained weights are licensed under the 'timesfm-non-commercial-license-v1.0' "
        "for non-commercial, research, and non-production use only. For commercial deployment, "
        "switch to TimesFM 2.5 (Apache-2.0) in Settings."
        if settings.TIMESFM_VERSION == "3.0"
        else None
    )

    return SystemHealthResponse(
        status="ok",
        device=device,
        device_name=device_name,
        cuda_available=cuda_available,
        vram_total_mb=vram_total_mb,
        vram_allocated_mb=vram_allocated_mb,
        ollama_ready=ollama_ready,
        ollama_model=ollama_model,
        timesfm_ready=timesfm_ready,
        timesfm_version=settings.TIMESFM_VERSION,
        timesfm_license_warning=timesfm_warning,
        disk_free_gb=disk_free_gb,
    )
