# Human-In-The-Loop (HITL) Analysis Workflow

## Overview

The HITL workflow enables iterative, step-by-step data analysis with human review and refinement at each stage. This allows users to guide the analysis process, provide feedback, and refine results before moving to the next section.

## Architecture

### Components

1. **Backend Workflow** (`hitl_analysis_workflow.py`)
   - LangGraph-based workflow with state management
   - Nodes: data profiling, planning, section generation, human review, finalization
   - Supports checkpointing and state persistence

2. **FastAPI Backend** (`hitl_analysis.py`, `backend_main.py`)
   - RESTful API endpoints for workflow control
   - Server-Sent Events (SSE) for real-time streaming
   - State management and thread-based execution

3. **Streamlit Frontend** (`hitl_analysis_ui.py`)
   - Interactive UI for workflow control
   - Plan editing, section review, feedback submission
   - Visualization display and comparison view

## Features

### Core Workflow States

- **Planning**: LLM generates analysis plan (list of analysis steps/sections)
- **Generation**: Generate one section at a time (analysis, visualization, or insight)
- **Review**: Pause for human review after each section
- **Iteration**: Support approval, feedback, edits, and regeneration
- **Finalization**: Compile final report after all sections are approved

### Human Interaction Features

- **Plan Editing**: Add, delete, reorder, and edit plan items
- **Section Editing**: Direct text editing of generated content
- **General Feedback**: Text feedback for regeneration
- **Sentence-level Feedback**: Select text and provide specific feedback
- **Multiple Feedbacks**: Support multiple sentence-level feedbacks per section
- **Comparison View**: Compare original vs. modified versions (planned)
- **Version Tracking**: Track first generated vs. modified versions (planned)

## Setup

### Prerequisites

```bash
pip install fastapi uvicorn streamlit requests langgraph langchain-groq pandas plotly
```

### Environment Variables

```bash
export GROQ_API_KEY="your_groq_api_key"
export API_BASE_URL="http://localhost:8000"  # For Streamlit frontend
```

### Running the System

1. **Start FastAPI Backend**:
   ```bash
   python backend_main.py
   ```
   The API will be available at `http://localhost:8000`

2. **Start Streamlit Frontend**:
   ```bash
   streamlit run main_app.py
   ```
   Navigate to the "🔄 HITL Analysis" tab

## Usage

### Starting a Workflow

1. Upload a CSV dataset
2. Enter your analysis request/query
3. Click "Start HITL Workflow"
4. System will:
   - Profile the data
   - Generate an analysis plan
   - Start generating the first section

### Reviewing Sections

After each section is generated:

1. **Review the section**:
   - Read the content
   - View visualizations
   - Check analysis results

2. **Provide feedback** (optional):
   - **Approve & Continue**: Move to next section
   - **Edit Mode**: Directly edit the content
   - **General Feedback**: Provide text feedback for regeneration
   - **Sentence Feedback**: Select specific text and provide targeted feedback

3. **Submit feedback**:
   - Click "Submit Feedback & Regenerate" to regenerate with feedback
   - Or click "Approve & Continue" to proceed

### Editing the Plan

- Use the sidebar to:
  - Edit step titles
  - Add new steps
  - Delete steps
  - Reorder steps
- Click "Save Plan Changes" to update the workflow

### Final Report

Once all sections are approved:
- System compiles final report
- Download as Markdown file
- Report includes all sections with visualizations

## API Endpoints

### POST `/hitl/start`
Start a new HITL workflow.

**Request:**
```json
{
  "human_request": "Analyze sales trends",
  "file_path": "data.csv"
}
```

**Response:**
```json
{
  "thread_id": "uuid-string"
}
```

### POST `/hitl/resume`
Resume workflow after human review.

**Request:**
```json
{
  "thread_id": "uuid-string",
  "review_action": "approved" | "feedback",
  "human_comment": "Optional feedback text",
  "edited_content": "Optional edited content",
  "updated_plan": ["Optional", "updated", "plan", "items"],
  "sentence_feedback": [
    {"text": "selected text", "feedback": "feedback text"}
  ]
}
```

### GET `/hitl/stream/{thread_id}`
Stream workflow execution with Server-Sent Events.

**Events:**
- `status`: Workflow step updates
- `data_profile`: Dataset profiling results
- `plan`: Analysis plan
- `section`: Generated section data
- `user_feedback`: Pause point for review
- `finished`: Final report
- `error`: Error messages

### GET `/hitl/state/{thread_id}`
Get current workflow state.

## Workflow Graph Structure

```
START
  ↓
data_profiling
  ↓
planning
  ↓
generate_section
  ↓
human_review (INTERRUPT)
  ↓
[router]
  ├─→ "approved" → check if more sections
  │     ├─→ "next_section" → update_index → generate_section
  │     └─→ "finalize" → finalize → END
  └─→ "feedback" → "revise" → generate_section
```

## Integration with Existing System

The HITL workflow integrates with existing agents:

- **Planner Agent**: Used for planning_node (generates JSON plans)
- **Executor Agent**: Used in generate_section_node for analysis execution
- **Visualizer Agent**: Used in generate_section_node for chart generation
- **Insight Generator**: Used in generate_section_node for insight generation

The existing automated workflow (`main.py`) remains intact as a fallback option.

## Section Structure

Each generated section is a dictionary:

```python
{
    "section_title": str,  # From plan
    "content": str,  # Markdown text description
    "analysis_type": str,  # "time_series", "correlation", etc.
    "analysis_results": dict,  # Structured results (stats, metrics)
    "visualizations": List[dict],  # Chart data/configs
    "insights": str,  # Generated insights text
    "raw_data": Optional[pd.DataFrame]  # If applicable
}
```

## Error Handling

- Graph state persistence (checkpoint recovery)
- Disconnection handling (reconnect to stream)
- Plan update validation (prevent index errors)
- Agent failure handling (retry, fallback)

## Future Enhancements

- Phase 2: Enhanced plan editing features
- Phase 3: Advanced sentence-level feedback
- Phase 4: Comparison view and version tracking
- Phase 5: UI polish and error handling improvements

## Notes

- The workflow uses LangGraph's `MemorySaver` for state persistence
- Interrupts are configured before the `human_review` node
- State is thread-based for concurrent workflows
- Visualizations are saved as both PNG and HTML (Plotly)

