from fastapi import APIRouter

from app.api.v1 import auth, curriculum, learning, me, profile, workspaces
from app.modules.evidence import router as evidence_router
from app.modules.learning_goal_resolution import router as goal_resolution_router
from app.modules.pretests import router as pretests_router
from app.modules.posttests import router as posttests_router
from app.modules.review import router as review_router
from app.modules.analytics import router as analytics_router

api_router = APIRouter()
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(curriculum.router, tags=["curriculum"])
api_router.include_router(goal_resolution_router.router, tags=["learning-goals"])
api_router.include_router(pretests_router.router, tags=["pretests"])
api_router.include_router(posttests_router, tags=["posttests"])
api_router.include_router(evidence_router.router, tags=["evidence"])
api_router.include_router(learning.router, tags=["learning"])
api_router.include_router(me.router, tags=["me"])
api_router.include_router(profile.router, tags=["profile"])
api_router.include_router(workspaces.router, tags=["workspaces"])
api_router.include_router(review_router.router, tags=["review"])
api_router.include_router(analytics_router.router, tags=["analytics"])
