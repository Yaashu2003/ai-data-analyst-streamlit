import gemini_patch
# hitl_analysis.py
# FastAPI router for HITL analysis workflow

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import json
import uuid
import os
from datetime import datetime

from hitl_analysis_workflow import create_hitl_workflow, HITLAnalysisWorkflowState
import pandas as pd

router = APIRouter(prefix="/hitl", tags=["HITL Analysis"])

# In-memory storage for workflow states (in production, use Redis or database)
WORKFLOW_STATES: Dict[str, Dict] = {}
WORKFLOW_APPS: Dict[str, Any] = {}

# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class StartRequest(BaseModel):
    human_request: str
    file_path: str

class StartResponse(BaseModel):
    thread_id: str

class SentenceFeedback(BaseModel):
    text: str
    feedback: str

class ResumeRequest(BaseModel):
    thread_id: str
    review_action: str  # "approved" or "feedback"
    human_comment: Optional[str] = None
    edited_content: Optional[str] = None
    updated_plan: Optional[List[str]] = None
    sentence_feedback: Optional[List[SentenceFeedback]] = None

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def get_gemini_api_key() -> str:
    """Get Gemini API key from environment"""
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY not found in environment")
    return api_key

def make_json_serializable(obj):
    """Convert pandas/numpy objects to JSON serializable format"""
    if isinstance(obj, dict):
        return {k: make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [make_json_serializable(v) for v in obj]
    elif isinstance(obj, pd.DataFrame):
        return obj.to_dict(orient='records')
    elif hasattr(obj, 'dtype'):
        return str(obj)
    elif isinstance(obj, (pd.Timestamp, pd.Period)):
        return str(obj)
    else:
        return obj

# =============================================================================
# API ENDPOINTS
# =============================================================================

@router.post("/start", response_model=StartResponse)
async def start_hitl_workflow(request: StartRequest):
    """Start a new HITL analysis workflow"""
    try:
        # Validate file exists
        if not os.path.exists(request.file_path):
            raise HTTPException(status_code=404, detail=f"File not found: {request.file_path}")
        
        # Create thread ID
        thread_id = str(uuid.uuid4())
        
        # Get API key
        gemini_api_key = get_gemini_api_key()
        
        # Create workflow
        app, workflow_instance = create_hitl_workflow(gemini_api_key)
        WORKFLOW_APPS[thread_id] = app
        
        # Initialize state
        initial_state: HITLAnalysisWorkflowState = {
            'user_query': request.human_request,
            'file_path': request.file_path,
            'dataset': None,
            'data_profile': None,
            'plan': [],
            'plan_details': None,
            'current_section_index': 0,
            'generated_sections': [],
            'human_feedback': None,
            'edited_content': None,
            'sentence_feedback': None,
            'approval_status': 'pending',
            'final_output': None,
            'final_output_original': None,
            'current_step': 'initialized',
            'error_log': []
        }
        
        # Store initial state
        config = {"configurable": {"thread_id": thread_id}}
        WORKFLOW_STATES[thread_id] = {
            'state': initial_state,
            'config': config,
            'created_at': datetime.now().isoformat()
        }
        
        return StartResponse(thread_id=thread_id)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error starting workflow: {str(e)}")

@router.post("/resume")
async def resume_hitl_workflow(request: ResumeRequest):
    """Resume workflow after human review"""
    try:
        thread_id = request.thread_id
        
        if thread_id not in WORKFLOW_STATES:
            raise HTTPException(status_code=404, detail="Thread ID not found")
        
        if thread_id not in WORKFLOW_APPS:
            raise HTTPException(status_code=404, detail="Workflow app not found")
        
        app = WORKFLOW_APPS[thread_id]
        config = WORKFLOW_STATES[thread_id]['config']
        
        # Get current state
        current_state = app.get_state(config)
        state_values = current_state.values if current_state else {}
        
        # Update state with human input
        updates = {}
        
        if request.updated_plan:
            updates['plan'] = request.updated_plan
            # Adjust current_section_index if plan changed
            if state_values.get('current_section_index', 0) >= len(request.updated_plan):
                updates['current_section_index'] = len(request.updated_plan) - 1
        
        if request.edited_content:
            updates['edited_content'] = request.edited_content
        
        if request.human_comment:
            updates['human_feedback'] = request.human_comment
        
        if request.sentence_feedback:
            updates['sentence_feedback'] = [{'text': sf.text, 'feedback': sf.feedback} for sf in request.sentence_feedback]
        
        updates['approval_status'] = request.review_action
        
        # Update state
        if updates:
            app.update_state(config, updates)
        
        # Resume workflow
        # The workflow will continue from the interrupt point
        # We'll handle streaming in the stream endpoint
        
        return {"thread_id": thread_id, "status": "resumed"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error resuming workflow: {str(e)}")

@router.get("/stream/{thread_id}")
async def stream_hitl_workflow(thread_id: str):
    """Stream workflow execution with SSE"""
    if thread_id not in WORKFLOW_APPS:
        raise HTTPException(status_code=404, detail="Thread ID not found")
    
    app = WORKFLOW_APPS[thread_id]
    config = WORKFLOW_STATES.get(thread_id, {}).get('config', {"configurable": {"thread_id": thread_id}})
    
    async def event_generator():
        try:
            # Get current state
            current_state = app.get_state(config)
            state_values = current_state.values if current_state else {}
            
            # Check if we need to resume
            if current_state and hasattr(current_state, 'next') and current_state.next:
                # Resume from interrupt
                async for event in app.astream(None, config, stream_mode="values"):
                    state = event
                    
                    # Send status update
                    yield f"data: {json.dumps({'type': 'status', 'data': {'step': state.get('current_step', 'unknown')}})}\n\n"
                    
                    # Send section data if generated
                    if state.get('current_step') == 'section_generated':
                        current_idx = state.get('current_section_index', 0)
                        sections = state.get('generated_sections', [])
                        if current_idx < len(sections) and sections[current_idx]:
                            section = sections[current_idx]
                            yield f"data: {json.dumps({'type': 'section', 'data': make_json_serializable(section)})}\n\n"
                    
                    # Send plan if available
                    if state.get('plan'):
                        yield f"data: {json.dumps({'type': 'plan', 'data': state['plan']})}\n\n"
                    
                    # Check if paused for review
                    if state.get('current_step') == 'awaiting_human_review':
                        yield f"data: {json.dumps({'type': 'user_feedback', 'data': {'plan': state.get('plan', []), 'current_index': state.get('current_section_index', 0), 'generated_sections': make_json_serializable(state.get('generated_sections', [])), 'current_chunk': make_json_serializable(state.get('generated_sections', [])[state.get('current_section_index', 0)] if state.get('generated_sections') and state.get('current_section_index', 0) < len(state.get('generated_sections', [])) else None)}})}\n\n"
                        break
                    
                    # Check if paused for final report review
                    if state.get('current_step') == 'awaiting_final_report_review':
                        yield f"data: {json.dumps({'type': 'final_report_review', 'data': {'final_output': state.get('final_output', ''), 'final_output_original': state.get('final_output_original', '')}})}\n\n"
                        break
                    
                    # Check if final report regenerated
                    if state.get('current_step') == 'final_report_regenerated':
                        yield f"data: {json.dumps({'type': 'final_report_regenerated', 'data': {'final_output': state.get('final_output', '')}})}\n\n"
                        # Go back to review
                        state['current_step'] = 'awaiting_final_report_review'
                        yield f"data: {json.dumps({'type': 'final_report_review', 'data': {'final_output': state.get('final_output', ''), 'final_output_original': state.get('final_output_original', '')}})}\n\n"
                        break
                    
                    # Check if finalized (legacy, should not happen with new flow)
                    if state.get('current_step') == 'finalized':
                        yield f"data: {json.dumps({'type': 'finished', 'data': {'final_output': state.get('final_output', '')}})}\n\n"
                        break
            else:
                # Initial run
                initial_state = WORKFLOW_STATES.get(thread_id, {}).get('state', {})
                async for event in app.astream(initial_state, config, stream_mode="values"):
                    state = event
                    
                    # Send status update
                    yield f"data: {json.dumps({'type': 'status', 'data': {'step': state.get('current_step', 'unknown')}})}\n\n"
                    
                    # Send data profile if available
                    if state.get('data_profile'):
                        yield f"data: {json.dumps({'type': 'data_profile', 'data': make_json_serializable(state['data_profile'])})}\n\n"
                    
                    # Send plan if available
                    if state.get('plan'):
                        yield f"data: {json.dumps({'type': 'plan', 'data': state['plan']})}\n\n"
                    
                    # Send section data if generated
                    if state.get('current_step') == 'section_generated':
                        current_idx = state.get('current_section_index', 0)
                        sections = state.get('generated_sections', [])
                        if current_idx < len(sections) and sections[current_idx]:
                            section = sections[current_idx]
                            yield f"data: {json.dumps({'type': 'section', 'data': make_json_serializable(section)})}\n\n"
                    
                    # Check if paused for review
                    if state.get('current_step') == 'awaiting_human_review':
                        yield f"data: {json.dumps({'type': 'user_feedback', 'data': {'plan': state.get('plan', []), 'current_index': state.get('current_section_index', 0), 'generated_sections': make_json_serializable(state.get('generated_sections', [])), 'current_chunk': make_json_serializable(state.get('generated_sections', [])[state.get('current_section_index', 0)] if state.get('generated_sections') and state.get('current_section_index', 0) < len(state.get('generated_sections', [])) else None)}})}\n\n"
                        break
                    
                    # Check if paused for final report review
                    if state.get('current_step') == 'awaiting_final_report_review':
                        yield f"data: {json.dumps({'type': 'final_report_review', 'data': {'final_output': state.get('final_output', ''), 'final_output_original': state.get('final_output_original', '')}})}\n\n"
                        break
                    
                    # Check if final report regenerated
                    if state.get('current_step') == 'final_report_regenerated':
                        yield f"data: {json.dumps({'type': 'final_report_regenerated', 'data': {'final_output': state.get('final_output', '')}})}\n\n"
                        # Go back to review
                        state['current_step'] = 'awaiting_final_report_review'
                        yield f"data: {json.dumps({'type': 'final_report_review', 'data': {'final_output': state.get('final_output', ''), 'final_output_original': state.get('final_output_original', '')}})}\n\n"
                        break
                    
                    # Check if finalized (legacy, should not happen with new flow)
                    if state.get('current_step') == 'finalized':
                        yield f"data: {json.dumps({'type': 'finished', 'data': {'final_output': state.get('final_output', '')}})}\n\n"
                        break
        
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'data': {'message': str(e)}})}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

@router.get("/state/{thread_id}")
async def get_workflow_state(thread_id: str):
    """Get current workflow state"""
    if thread_id not in WORKFLOW_APPS:
        raise HTTPException(status_code=404, detail="Thread ID not found")
    
    app = WORKFLOW_APPS[thread_id]
    config = WORKFLOW_STATES.get(thread_id, {}).get('config', {"configurable": {"thread_id": thread_id}})
    
    current_state = app.get_state(config)
    state_values = current_state.values if current_state else {}
    
    return {
        "thread_id": thread_id,
        "state": make_json_serializable(state_values),
        "next": current_state.next if current_state else []
    }


