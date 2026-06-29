# Project Structure Documentation

## 📁 Complete File Structure and Purpose

This document provides a detailed overview of all files in the Data Analyst project, their purpose, and what they contain.

---

## 🎯 **Main Application Files**

### `main_app.py`
**Purpose**: Main Streamlit application entry point  
**Contains**:
- Streamlit UI configuration and page setup
- Tab navigation (Automated Analysis, Chatbot, Report Viewer, HITL Analysis)
- Session state initialization
- Integration of all major components
- File upload handling
- Database initialization
- Main application orchestration

**Key Functions**:
- `load_data()`: Loads CSV data
- `init_db()`: Initializes SQLite database
- Tab routing and UI rendering

---

## 🤖 **Core Agent Files**

### `planner.py`
**Purpose**: Intelligent Analysis Planner Agent  
**Contains**:
- `IntelligentAnalysisPlanner` class
- Analysis plan generation using LLM (Groq/Gemini)
- Converts user queries into structured analysis plans
- Generates JSON-formatted analysis steps
- Plan validation and refinement

**Key Functions**:
- `create_plan()`: Generates analysis plan from user query
- `refine_plan()`: Refines plan based on feedback

---

### `executor.py`
**Purpose**: Analysis Execution Agent  
**Contains**:
- `IntelligentAnalysisExecutor` class
- SQL query generation and execution
- Data analysis operations
- Statistical calculations
- Results formatting

**Key Functions**:
- `execute_analysis()`: Executes analysis based on plan
- `generate_sql()`: Creates SQL queries
- `run_analysis()`: Runs analysis operations

---

### `visualizer.py`
**Purpose**: Comprehensive Visualization Generator  
**Contains**:
- `ComprehensiveVisualizationGenerator` class
- Chart generation (Plotly, Matplotlib)
- Multiple chart types (bar, line, scatter, heatmap, etc.)
- HTML chart export
- Chart optimization and styling

**Key Functions**:
- `generate_visualization()`: Creates charts
- `save_chart()`: Saves charts to files
- `generate_chart_html()`: Creates HTML chart files

---

### `insights.py`
**Purpose**: Enhanced Insight Generator  
**Contains**:
- `EnhancedInsightGenerator` class
- LLM-powered insight generation
- Pattern recognition
- Business intelligence insights
- Natural language insight formatting

**Key Functions**:
- `generate_insights()`: Creates insights from analysis
- `identify_patterns()`: Finds data patterns
- `format_insights()`: Formats insights for display

---

### `orchestrator.py`
**Purpose**: Multi-Agent Orchestration  
**Contains**:
- Agent coordination logic
- Workflow management
- Agent communication
- Task distribution

---

## 📊 **Report Generation Files**

### `report.py`
**Purpose**: Interactive HTML Report Generator  
**Contains**:
- HTML report template generation
- Report structure and styling
- Chart embedding
- Interactive report creation
- CSS styling for reports

**Key Functions**:
- `generate_interactive_report()`: Creates full HTML report
- `embed_charts()`: Embeds visualizations
- `style_report()`: Applies styling

---

### `report_viewer.py`
**Purpose**: Report Viewer with Editing & Feedback  
**Contains**:
- Report display in Streamlit
- **Edit & Feedback Tab**:
  - Text editing capabilities
  - Sentence-level feedback popup (select text → popup appears)
  - Multiple feedback collection
  - General feedback input
- **Final Report Tab**:
  - Final report display
  - Download options (HTML, Markdown, Text)
  - Comparison view (git-style diff)
- Report state management
- Feedback processing and storage

**Key Functions**:
- `render_report_tab()`: Main report viewer
- `render_report_editing_ui()`: Edit and feedback UI
- `generate_final_report_with_feedback()`: LLM-based report regeneration
- `create_git_style_diff()`: Git-style comparison
- `create_word_level_diff()`: Word-level comparison
- `convert_markdown_to_html_report()`: Markdown to HTML conversion

**Features**:
- ✅ Text selection popup for feedback
- ✅ Multiple feedback support
- ✅ Direct text editing
- ✅ Git-style diff comparison
- ✅ Final report generation with all feedback

---

### `report_html.py`
**Purpose**: HTML report utilities  
**Contains**:
- HTML manipulation functions
- Report formatting helpers

---

## 🔄 **HITL (Human-In-The-Loop) Files**

### `hitl_analysis_workflow.py`
**Purpose**: LangGraph HITL Workflow Definition  
**Contains**:
- `HITLAnalysisWorkflowState`: TypedDict state definition
- LangGraph workflow nodes:
  - `data_profiling_node`: Profiles dataset
  - `planning_node`: Generates analysis plan
  - `generate_section_node`: Generates individual sections
  - `human_review_node`: Pause point for review
  - `finalize_node`: Compiles final report
  - `final_report_review_node`: Review final report
  - `regenerate_final_report_node`: Regenerates based on feedback
- Router functions for workflow control
- Checkpoint management

**Key Functions**:
- `create_hitl_workflow()`: Creates and compiles workflow
- `review_router()`: Routes after section review
- `final_report_review_router()`: Routes after final review

---

### `hitl_analysis.py`
**Purpose**: FastAPI Backend for HITL Workflow  
**Contains**:
- FastAPI router with `/hitl` prefix
- API endpoints:
  - `POST /hitl/start`: Start new workflow
  - `POST /hitl/resume`: Resume workflow after review
  - `GET /hitl/stream/{thread_id}`: SSE streaming
  - `GET /hitl/state/{thread_id}`: Get current state
- State management (in-memory, can use Redis/DB)
- SSE event streaming
- JSON serialization utilities

**Key Functions**:
- `start_hitl_workflow()`: Starts workflow
- `resume_hitl_workflow()`: Resumes workflow
- `stream_hitl_workflow()`: SSE streaming
- `make_json_serializable()`: Converts state to JSON

---

### `hitl_analysis_ui.py`
**Purpose**: Streamlit UI for HITL Workflow  
**Contains**:
- HITL workflow UI components
- File upload and workflow start
- Plan sidebar with editing
- Section display with visualizations
- Review controls (approve, feedback, edit)
- Sentence-level feedback support
- Comparison view
- Final report review UI

**Key Functions**:
- `render_hitl_analysis_ui()`: Main HITL UI
- `render_plan_sidebar()`: Plan editor
- `render_section_content()`: Section display
- `render_review_controls()`: Review UI
- `start_hitl_workflow()`: API call to start
- `resume_hitl_workflow()`: API call to resume
- `stream_hitl_workflow()`: SSE streaming handler

---

### `backend_main.py`
**Purpose**: FastAPI Application Server  
**Contains**:
- FastAPI app initialization
- CORS middleware configuration
- Router registration
- Health check endpoints

**Key Endpoints**:
- `GET /`: API info
- `GET /health`: Health check

---

## 💬 **Chatbot Files**

### `chatbot_agent.py`
**Purpose**: Chatbot Agent Creation  
**Contains**:
- LangChain agent setup
- Tool definitions for chatbot
- Agent configuration
- LLM integration

**Key Functions**:
- `create_agent()`: Creates chatbot agent

---

### `chatbot_conversation.py`
**Purpose**: Chatbot Conversation UI  
**Contains**:
- Streamlit chatbot interface
- Message history management
- Chat UI components
- Agent interaction handling

**Key Functions**:
- `display_chatbot()`: Main chatbot UI

---

### `chatbot_config.py`
**Purpose**: Chatbot Configuration  
**Contains**:
- Chatbot settings
- Configuration parameters

---

## 🛠️ **Utility Files**

### `utils.py`
**Purpose**: General Utilities  
**Contains**:
- Helper functions
- Common utilities
- Data processing helpers

---

### `storage.py`
**Purpose**: Data Storage Utilities  
**Contains**:
- Database operations
- File storage functions
- Data persistence

---

### `state.py`
**Purpose**: State Management  
**Contains**:
- Application state definitions
- State management utilities

---

### `data_agent_core.py`
**Purpose**: Core Data Agent Logic  
**Contains**:
- Core agent functionality
- Base agent classes

---

## 📈 **Analysis Files**

### `main.py`
**Purpose**: Main Analysis Script (Legacy/Alternative)  
**Contains**:
- Alternative analysis workflow
- Direct analysis execution
- Results saving

---

### `graph.py`
**Purpose**: LangGraph Workflow Definition (Alternative)  
**Contains**:
- Alternative graph structure
- Chart analysis workflow
- Loop control logic

---

### `ui.py`
**Purpose**: Alternative UI (One-shot)  
**Contains**:
- One-shot analysis UI
- Alternative workflow implementation

---

### `summarize.py`
**Purpose**: Summarization Utilities  
**Contains**:
- Text summarization functions
- Report summarization

---

### `anl_funcs.py`
**Purpose**: Analysis Functions  
**Contains**:
- Analysis helper functions
- Statistical functions

---

### `autoviz_charts.py`
**Purpose**: AutoViz Chart Generation  
**Contains**:
- AutoViz integration
- Automatic chart generation

---

## 📄 **Other Files**

### `Home.py`
**Purpose**: Home Page/Alternative Entry  
**Contains**:
- Alternative home page
- Report loading functions

---

### `1_Chatbot.py`
**Purpose**: Standalone Chatbot Script  
**Contains**:
- Independent chatbot implementation

---

### `downloadfile.py`
**Purpose**: File Download Utilities  
**Contains**:
- Download functionality

---

### `deploy.py`
**Purpose**: Deployment Script  
**Contains**:
- Deployment configuration

---

### `r.py`
**Purpose**: R Script Integration (if any)  
**Contains**:
- R language integration

---

## 📝 **Documentation Files**

### `README.md`
**Purpose**: Main project documentation

### `HITL_README.md`
**Purpose**: HITL workflow documentation

### `FINAL_REPORT_HITL_FEATURES.md`
**Purpose**: Final report HITL features documentation

### `IMPLEMENTATION_SUMMARY.md`
**Purpose**: Implementation summary

### `REPORT_EDITING_FEATURES.md`
**Purpose**: Report editing features documentation

---

## 🗂️ **Data Files**

### `Superstore.csv`
**Purpose**: Sample dataset for analysis

### `sales_temp.db` / `sales_data.db`
**Purpose**: SQLite database files for data storage

---

## 📊 **Output Files**

### `interactive_analysis_report.html`
**Purpose**: Generated interactive HTML report

### `charts/` directory
**Purpose**: Generated chart images (PNG)

### `charts_html/` directory
**Purpose**: Generated HTML chart files

### `autoviz/` directory
**Purpose**: AutoViz generated charts

---

## 🔧 **Configuration Files**

### `.env`
**Purpose**: Environment variables
- API keys (NVIDIA, Groq, Gemini, OpenAI)
- Configuration settings

---

## 📋 **Workflow Overview**

### **Automated Analysis Flow**:
1. User uploads CSV → `main_app.py`
2. Data loaded → `main.py` or `orchestrator.py`
3. Plan generated → `planner.py`
4. Analysis executed → `executor.py`
5. Visualizations created → `visualizer.py`
6. Insights generated → `insights.py`
7. Report generated → `report.py`
8. Report displayed → `report_viewer.py`

### **HITL Analysis Flow**:
1. User starts HITL → `hitl_analysis_ui.py`
2. Backend starts workflow → `hitl_analysis.py`
3. Workflow executes → `hitl_analysis_workflow.py`
4. Section generated → Uses `executor.py`, `visualizer.py`, `insights.py`
5. Human reviews → `hitl_analysis_ui.py`
6. Feedback processed → `hitl_analysis.py`
7. Final report → `hitl_analysis_workflow.py`

### **Chatbot Flow**:
1. User opens chatbot → `chatbot_conversation.py`
2. Agent created → `chatbot_agent.py`
3. Query processed → Agent uses tools
4. Response displayed → `chatbot_conversation.py`

---

## 🎯 **Key Features by File**

| File | Key Feature |
|------|-------------|
| `report_viewer.py` | **Text selection popup for feedback** |
| `hitl_analysis_workflow.py` | **Iterative workflow with human review** |
| `hitl_analysis_ui.py` | **Interactive HITL interface** |
| `planner.py` | **Intelligent plan generation** |
| `visualizer.py` | **Comprehensive chart generation** |
| `insights.py` | **LLM-powered insights** |
| `chatbot_agent.py` | **Conversational AI agent** |

---

## 📌 **Important Notes**

1. **Main Entry Point**: `main_app.py` is the primary Streamlit application
2. **Backend API**: `backend_main.py` runs the FastAPI server (port 8000)
3. **Frontend**: Streamlit runs on port 8501
4. **HITL Workflow**: Requires both backend and frontend running
5. **Report Editing**: Popup appears when text is selected in "Edit & Feedback" tab
6. **Multiple Feedbacks**: Can collect multiple sentence-level feedbacks

---

This documentation provides a comprehensive overview of the project structure. Each file serves a specific purpose in the data analysis workflow.
