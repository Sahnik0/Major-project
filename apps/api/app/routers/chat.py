from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.app.db import ChatMessage, Dataset, get_db
from apps.api.app.services.chat_advisor import answer_user_query, get_or_create_initial_messages

router = APIRouter(prefix="/datasets/{dataset_id}/chat", tags=["Chat Advisor"])


class ChatMessageResponse(BaseModel):
    id: str
    role: str
    content: str
    card_type: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    created_at: str


class ChatPromptRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)


@router.get("/messages", response_model=List[ChatMessageResponse])
def get_chat_messages(dataset_id: str, db: Session = Depends(get_db)):
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(
            status_code=404,
            detail={"code": "DATASET_NOT_FOUND", "message": f"Dataset '{dataset_id}' not found."},
        )

    msgs = get_or_create_initial_messages(dataset, db)
    return [
        ChatMessageResponse(
            id=m.id,
            role=m.role,
            content=m.content,
            card_type=m.card_type,
            payload=m.payload,
            created_at=m.created_at.isoformat(),
        )
        for m in msgs
    ]


@router.post("", response_model=ChatMessageResponse)
async def post_chat_message(
    dataset_id: str,
    req: ChatPromptRequest,
    db: Session = Depends(get_db),
):
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(
            status_code=404,
            detail={"code": "DATASET_NOT_FOUND", "message": f"Dataset '{dataset_id}' not found."},
        )

    # Save user message
    user_msg = ChatMessage(
        dataset_id=dataset_id,
        role="user",
        content=req.message,
    )
    db.add(user_msg)
    db.commit()

    # Generate assistant answer
    reply_text, card_type, card_payload = await answer_user_query(dataset, req.message, db)

    assistant_msg = ChatMessage(
        dataset_id=dataset_id,
        role="assistant",
        content=reply_text,
        card_type=card_type,
    )
    assistant_msg.payload = card_payload
    db.add(assistant_msg)
    db.commit()
    db.refresh(assistant_msg)

    return ChatMessageResponse(
        id=assistant_msg.id,
        role=assistant_msg.role,
        content=assistant_msg.content,
        card_type=assistant_msg.card_type,
        payload=assistant_msg.payload,
        created_at=assistant_msg.created_at.isoformat(),
    )


@router.delete("/messages")
def reset_chat(dataset_id: str, db: Session = Depends(get_db)):
    db.query(ChatMessage).filter(ChatMessage.dataset_id == dataset_id).delete()
    db.commit()
    return {"status": "ok", "message": "Conversation reset."}

