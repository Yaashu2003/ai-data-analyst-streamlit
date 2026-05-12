# backend_main.py
# FastAPI backend server for HITL analysis workflow

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from hitl_analysis import router as hitl_router
import uvicorn

app = FastAPI(title="HITL Data Analysis API", version="1.0.0")

# CORS middleware for Streamlit frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(hitl_router)

@app.get("/")
async def root():
    return {"message": "HITL Data Analysis API", "version": "1.0.0"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

