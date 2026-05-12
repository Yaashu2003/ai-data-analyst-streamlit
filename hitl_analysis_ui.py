# hitl_analysis_ui.py
# Streamlit UI component for HITL analysis workflow

import streamlit as st
import pandas as pd
import json
import requests
from typing import Dict, Any, List, Optional
import os
from pathlib import Path
import plotly.graph_objects as go
from plotly.io import from_json

# =============================================================================
# CONFIGURATION
# =============================================================================

# Backend API URL (adjust if running on different host/port)
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

# =============================================================================
# SESSION STATE INITIALIZATION
# =============================================================================

def init_session_state():
    """Initialize session state variables"""
    if 'hitl_thread_id' not in st.session_state:
        st.session_state.hitl_thread_id = None
    if 'hitl_plan' not in st.session_state:
        st.session_state.hitl_plan = []
    if 'hitl_sections' not in st.session_state:
        st.session_state.hitl_sections = []
    if 'hitl_current_index' not in st.session_state:
        st.session_state.hitl_current_index = 0
    if 'hitl_current_section' not in st.session_state:
        st.session_state.hitl_current_section = None
    if 'hitl_data_profile' not in st.session_state:
        st.session_state.hitl_data_profile = None
    if 'hitl_final_output' not in st.session_state:
        st.session_state.hitl_final_output = None
    if 'hitl_edit_mode' not in st.session_state:
        st.session_state.hitl_edit_mode = False
    if 'hitl_edited_content' not in st.session_state:
        st.session_state.hitl_edited_content = ""
    if 'hitl_sentence_feedbacks' not in st.session_state:
        st.session_state.hitl_sentence_feedbacks = []
    if 'hitl_comparison_view' not in st.session_state:
        st.session_state.hitl_comparison_view = False
    if 'hitl_final_sentence_feedbacks' not in st.session_state:
        st.session_state.hitl_final_sentence_feedbacks = []
    if 'hitl_feedback_open' not in st.session_state:
        st.session_state.hitl_feedback_open = False
    if 'hitl_final_feedback_open' not in st.session_state:
        st.session_state.hitl_final_feedback_open = False

# =============================================================================
# API CLIENT FUNCTIONS
# =============================================================================

def start_hitl_workflow(human_request: str, file_path: str) -> Optional[str]:
    """Start HITL workflow via API"""
    try:
        response = requests.post(
            f"{API_BASE_URL}/hitl/start",
            json={"human_request": human_request, "file_path": file_path}
        )
        response.raise_for_status()
        data = response.json()
        return data.get("thread_id")
    except Exception as e:
        st.error(f"Error starting workflow: {str(e)}")
        return None

def resume_hitl_workflow(
    thread_id: str,
    review_action: str,
    human_comment: Optional[str] = None,
    edited_content: Optional[str] = None,
    updated_plan: Optional[List[str]] = None,
    sentence_feedback: Optional[List[Dict]] = None
) -> bool:
    """Resume HITL workflow via API"""
    try:
        payload = {
            "thread_id": thread_id,
            "review_action": review_action
        }
        if human_comment:
            payload["human_comment"] = human_comment
        if edited_content:
            payload["edited_content"] = edited_content
        if updated_plan:
            payload["updated_plan"] = updated_plan
        if sentence_feedback:
            payload["sentence_feedback"] = sentence_feedback
        
        response = requests.post(
            f"{API_BASE_URL}/hitl/resume",
            json=payload
        )
        response.raise_for_status()
        return True
    except Exception as e:
        st.error(f"Error resuming workflow: {str(e)}")
        return False

def get_workflow_state(thread_id: str) -> Optional[Dict]:
    """Get current workflow state"""
    try:
        response = requests.get(f"{API_BASE_URL}/hitl/state/{thread_id}")
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"Error getting state: {str(e)}")
        return None

# =============================================================================
# UI COMPONENTS
# =============================================================================

def render_plan_sidebar():
    """Render editable plan sidebar"""
    with st.sidebar:
        st.header("📋 Analysis Plan")
        
        if st.session_state.hitl_plan:
            # Editable plan list
            edited_plan = []
            for i, item in enumerate(st.session_state.hitl_plan):
                col1, col2 = st.columns([4, 1])
                with col1:
                    edited_item = st.text_input(
                        f"Step {i+1}",
                        value=item,
                        key=f"plan_item_{i}",
                        label_visibility="collapsed"
                    )
                    edited_plan.append(edited_item)
                with col2:
                    if st.button("🗑️", key=f"delete_{i}"):
                        edited_plan.pop(i)
                        st.rerun()
            
            # Add new item
            if st.button("➕ Add Step"):
                edited_plan.append("New Analysis Step")
                st.rerun()
            
            # Reorder buttons
            if len(edited_plan) > 1:
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("⬆️ Move Up"):
                        idx = st.session_state.hitl_current_index
                        if idx > 0:
                            edited_plan[idx], edited_plan[idx-1] = edited_plan[idx-1], edited_plan[idx]
                            st.session_state.hitl_plan = edited_plan
                            st.rerun()
                with col2:
                    if st.button("⬇️ Move Down"):
                        idx = st.session_state.hitl_current_index
                        if idx < len(edited_plan) - 1:
                            edited_plan[idx], edited_plan[idx+1] = edited_plan[idx+1], edited_plan[idx]
                            st.session_state.hitl_plan = edited_plan
                            st.rerun()
            
            # Update plan button
            if edited_plan != st.session_state.hitl_plan:
                if st.button("💾 Save Plan Changes"):
                    st.session_state.hitl_plan = edited_plan
                    if st.session_state.hitl_thread_id:
                        resume_hitl_workflow(
                            st.session_state.hitl_thread_id,
                            "approved",  # Keep current status
                            updated_plan=edited_plan
                        )
                    st.success("Plan updated!")
                    st.rerun()
        else:
            st.info("Plan will appear here once workflow starts")

def render_section_content(section: Dict[str, Any]):
    """Render a generated section with charts and content"""
    if not section:
        return
    
    st.subheader(section.get('section_title', 'Untitled Section'))
    
    # Display content
    content = section.get('content', '')
    if content:
        st.markdown(content)
    
    # Display visualizations
    visualizations = section.get('visualizations', [])
    if visualizations:
        st.markdown("### 📊 Visualizations")
        for viz in visualizations:
            if 'error' not in viz:
                html_path = viz.get('html_path')
                png_path = viz.get('png_path')
                
                if html_path and os.path.exists(html_path):
                    # Try to load and display Plotly HTML
                    try:
                        with open(html_path, 'r', encoding='utf-8') as f:
                            html_content = f.read()
                        st.components.v1.html(html_content, height=500, scrolling=True)
                    except Exception:
                        # Fallback to image
                        if png_path and os.path.exists(png_path):
                            st.image(png_path)
                elif png_path and os.path.exists(png_path):
                    st.image(png_path)
    
    # Display analysis results as table if available
    analysis_results = section.get('analysis_results', {})
    if analysis_results and 'error' not in analysis_results:
        st.markdown("### 📈 Analysis Results")
        # Try to display as DataFrame if possible
        try:
            if isinstance(analysis_results, dict):
                # Convert to DataFrame if it's a simple dict
                df_results = pd.DataFrame([analysis_results])
                st.dataframe(df_results, use_container_width=True)
            else:
                st.json(analysis_results)
        except Exception:
            st.json(analysis_results)

def render_review_controls():
    """Render review controls (approve, feedback, edit)"""
    st.markdown("---")
    st.subheader("✏️ Review & Feedback")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("✅ Approve & Continue", type="primary", use_container_width=True):
            if st.session_state.hitl_thread_id:
                success = resume_hitl_workflow(
                    st.session_state.hitl_thread_id,
                    "approved"
                )
                if success:
                    st.success("Approved! Generating next section...")
                    st.rerun()
    
    with col2:
        if st.button("✏️ Edit Mode", use_container_width=True):
            st.session_state.hitl_edit_mode = not st.session_state.hitl_edit_mode
            st.rerun()
    
    with col3:
        if st.button("🔄 Compare Versions", use_container_width=True):
            st.session_state.hitl_comparison_view = not st.session_state.hitl_comparison_view
            st.rerun()
    
    # Edit mode
    if st.session_state.hitl_edit_mode:
        st.markdown("#### Direct Text Editing")
        current_section = st.session_state.hitl_current_section
        if current_section:
            edited = st.text_area(
                "Edit section content:",
                value=current_section.get('content', ''),
                height=300,
                key="edit_content"
            )
            st.session_state.hitl_edited_content = edited
    
    feedback_text = st.session_state.get("general_feedback", "")
    if st.button("📝 Feedback", use_container_width=True):
        st.session_state.hitl_feedback_open = not st.session_state.hitl_feedback_open
    
    if st.session_state.hitl_feedback_open:
        # General feedback
        st.markdown("#### General Feedback")
        feedback_text = st.text_area(
            "Provide feedback for regeneration:",
            value=feedback_text,
            placeholder="E.g., 'Make the insights more specific', 'Add more statistical details', etc.",
            height=100,
            key="general_feedback"
        )
        
        # Sentence-level feedback
        st.markdown("#### Sentence-Level Feedback")
        st.info("Select text in the section above and provide specific feedback")
        
        selected_text = st.text_input("Selected text:", key="selected_text")
        sentence_feedback = st.text_input("Feedback for this text:", key="sentence_feedback_input")
        
        if st.button("➕ Add Sentence Feedback"):
            if selected_text and sentence_feedback:
                st.session_state.hitl_sentence_feedbacks.append({
                    "text": selected_text,
                    "feedback": sentence_feedback
                })
                st.success("Sentence feedback added!")
                st.rerun()
        
        # Display added sentence feedbacks
        if st.session_state.hitl_sentence_feedbacks:
            st.markdown("**Added Feedbacks:**")
            for i, sf in enumerate(st.session_state.hitl_sentence_feedbacks):
                col1, col2 = st.columns([4, 1])
                with col1:
                    st.write(f"**Text:** \"{sf['text']}\" → **Feedback:** {sf['feedback']}")
                with col2:
                    if st.button("🗑️", key=f"remove_sf_{i}"):
                        st.session_state.hitl_sentence_feedbacks.pop(i)
                        st.rerun()
    
    # Submit feedback button
    if st.button("📤 Submit Feedback & Regenerate", type="secondary", use_container_width=True):
        if st.session_state.hitl_thread_id:
            success = resume_hitl_workflow(
                st.session_state.hitl_thread_id,
                "feedback",
                human_comment=feedback_text if feedback_text else None,
                edited_content=st.session_state.hitl_edited_content if st.session_state.hitl_edited_content else None,
                sentence_feedback=st.session_state.hitl_sentence_feedbacks if st.session_state.hitl_sentence_feedbacks else None
            )
            if success:
                st.success("Feedback submitted! Regenerating section...")
                # Reset feedback state
                st.session_state.hitl_edited_content = ""
                st.session_state.hitl_sentence_feedbacks = []
                st.session_state.hitl_edit_mode = False
                st.rerun()

def render_comparison_view(original: Dict, modified: Dict):
    """Render side-by-side comparison of original vs modified"""
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("### Original Version")
        render_section_content(original)
    
    with col2:
        st.markdown("### Modified Version")
        render_section_content(modified)

def render_final_report_review_controls():
    """Render review controls for final report (similar to section review)"""
    st.markdown("---")
    st.subheader("✏️ Final Report Review & Feedback")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("✅ Approve Final Report", type="primary", use_container_width=True):
            if st.session_state.hitl_thread_id:
                success = resume_hitl_workflow(
                    st.session_state.hitl_thread_id,
                    "approved"
                )
                if success:
                    st.success("Final report approved!")
                    st.rerun()
    
    with col2:
        if st.button("🔄 Compare Versions", use_container_width=True):
            st.session_state.hitl_comparison_view = not st.session_state.hitl_comparison_view
            st.rerun()
    
    with col3:
        if st.button("📋 View Original", use_container_width=True):
            # Get original from state
            state_data = get_workflow_state(st.session_state.hitl_thread_id)
            if state_data:
                original = state_data.get('state', {}).get('final_output_original', '')
                if original:
                    with st.expander("Original Report", expanded=True):
                        st.markdown(original)
    
    feedback_text = st.session_state.get("final_report_feedback", "")
    if st.button("📝 Feedback", use_container_width=True, key="final_feedback_button"):
        st.session_state.hitl_final_feedback_open = not st.session_state.hitl_final_feedback_open
    
    if st.session_state.hitl_final_feedback_open:
        # General feedback
        st.markdown("#### General Feedback")
        feedback_text = st.text_area(
            "Provide feedback for report regeneration:",
            value=feedback_text,
            placeholder="E.g., 'Make the executive summary more concise', 'Add more statistical details', 'Improve the insights section', etc.",
            height=100,
            key="final_report_feedback"
        )
        
        # Sentence-level feedback
        st.markdown("#### Sentence-Level Feedback")
        st.info("Select text in the report above and provide specific feedback")
        
        selected_text = st.text_input("Selected text:", key="final_selected_text")
        sentence_feedback = st.text_input("Feedback for this text:", key="final_sentence_feedback_input")
        
        if st.button("➕ Add Sentence Feedback", key="add_final_sentence_feedback"):
            if selected_text and sentence_feedback:
                if 'hitl_final_sentence_feedbacks' not in st.session_state:
                    st.session_state.hitl_final_sentence_feedbacks = []
                st.session_state.hitl_final_sentence_feedbacks.append({
                    "text": selected_text,
                    "feedback": sentence_feedback
                })
                st.success("Sentence feedback added!")
                st.rerun()
        
        # Display added sentence feedbacks
        if 'hitl_final_sentence_feedbacks' in st.session_state and st.session_state.hitl_final_sentence_feedbacks:
            st.markdown("**Added Feedbacks:**")
            for i, sf in enumerate(st.session_state.hitl_final_sentence_feedbacks):
                col1, col2 = st.columns([4, 1])
                with col1:
                    st.write(f"**Text:** \"{sf['text']}\" → **Feedback:** {sf['feedback']}")
                with col2:
                    if st.button("🗑️", key=f"remove_final_sf_{i}"):
                        st.session_state.hitl_final_sentence_feedbacks.pop(i)
                        st.rerun()
    
    # Comparison view
    if st.session_state.hitl_comparison_view:
        st.markdown("---")
        st.markdown("### 📊 Version Comparison")
        state_data = get_workflow_state(st.session_state.hitl_thread_id)
        if state_data:
            original = state_data.get('state', {}).get('final_output_original', '')
            current = st.session_state.hitl_final_output
            
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("#### Original Version")
                st.markdown(original)
            with col2:
                st.markdown("#### Current Version")
                st.markdown(current)
    
    # Submit feedback button
    if st.button("📤 Submit Feedback & Regenerate Report", type="secondary", use_container_width=True):
        if st.session_state.hitl_thread_id:
            final_sentence_feedbacks = st.session_state.get('hitl_final_sentence_feedbacks', [])
            success = resume_hitl_workflow(
                st.session_state.hitl_thread_id,
                "feedback",
                human_comment=feedback_text if feedback_text else None,
                edited_content=st.session_state.hitl_edited_content if st.session_state.get('hitl_edited_content') else None,
                sentence_feedback=final_sentence_feedbacks if final_sentence_feedbacks else None
            )
            if success:
                st.success("Feedback submitted! Regenerating final report...")
                # Reset feedback state
                st.session_state.hitl_edited_content = ""
                if 'hitl_final_sentence_feedbacks' in st.session_state:
                    st.session_state.hitl_final_sentence_feedbacks = []
                st.session_state.hitl_edit_mode = False
                st.rerun()

# =============================================================================
# MAIN UI FUNCTION
# =============================================================================

def render_hitl_analysis_ui():
    """Main function to render HITL analysis UI"""
    init_session_state()
    
    st.title("🔄 Human-In-The-Loop Analysis Workflow")
    st.markdown("Iterative, step-by-step data analysis with human review and refinement")
    
    # File upload and start section
    with st.expander("🚀 Start New Analysis", expanded=not st.session_state.hitl_thread_id):
        col1, col2 = st.columns([2, 1])
        
        with col1:
            user_query = st.text_area(
                "Analysis Request:",
                placeholder="E.g., 'Analyze sales trends and identify key drivers of profitability'",
                height=100,
                key="user_query_input"
            )
        
        with col2:
            uploaded_file = st.file_uploader("Upload Dataset (CSV)", type=['csv'], key="hitl_file_upload")
        
        if st.button("▶️ Start HITL Workflow", type="primary", disabled=not (user_query and uploaded_file)):
            if uploaded_file:
                # Save uploaded file
                file_path = f"temp_{uploaded_file.name}"
                with open(file_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                
                # Start workflow
                with st.spinner("Starting workflow..."):
                    thread_id = start_hitl_workflow(user_query, file_path)
                    if thread_id:
                        st.session_state.hitl_thread_id = thread_id
                        st.success(f"Workflow started! Thread ID: {thread_id}")
                        st.rerun()
    
    # Main workflow display
    if st.session_state.hitl_thread_id:
        # Render plan sidebar
        render_plan_sidebar()
        
        # Main content area
        st.markdown("---")
        
        # Progress indicator
        if st.session_state.hitl_plan:
            total_steps = len(st.session_state.hitl_plan)
            current_step = st.session_state.hitl_current_index + 1
            progress = current_step / total_steps if total_steps > 0 else 0
            st.progress(progress)
            st.caption(f"Section {current_step} of {total_steps}")
        
        # Display completed sections
        if st.session_state.hitl_sections:
            st.markdown("### ✅ Completed Sections")
            for i, section in enumerate(st.session_state.hitl_sections):
                if section and i < st.session_state.hitl_current_index:
                    with st.expander(f"Section {i+1}: {section.get('section_title', 'Untitled')}", expanded=False):
                        render_section_content(section)
        
        # Display current section
        if st.session_state.hitl_current_section:
            st.markdown("### 📝 Current Section (Under Review)")
            
            # Comparison view toggle
            if st.session_state.hitl_comparison_view:
                # For now, show original vs current (in real implementation, track versions)
                st.info("Comparison view: Original vs Current")
                render_section_content(st.session_state.hitl_current_section)
            else:
                render_section_content(st.session_state.hitl_current_section)
            
            # Review controls
            render_review_controls()
        
        # Final output with HITL features
        if st.session_state.hitl_final_output:
            st.markdown("---")
            st.markdown("### 🎉 Final Report")
            
            # Check if we're in final report review mode
            state_data = get_workflow_state(st.session_state.hitl_thread_id) if st.session_state.hitl_thread_id else None
            is_final_review = False
            if state_data:
                current_step = state_data.get('state', {}).get('current_step', '')
                is_final_review = current_step == 'awaiting_final_report_review' or current_step == 'final_report_regenerated'
            
            # Display final report with edit mode
            if is_final_review:
                # Edit mode toggle
                col1, col2 = st.columns([3, 1])
                with col2:
                    if st.button("✏️ Toggle Edit Mode", use_container_width=True):
                        st.session_state.hitl_edit_mode = not st.session_state.hitl_edit_mode
                        st.rerun()
                
                if st.session_state.hitl_edit_mode:
                    # Editable text area
                    edited_report = st.text_area(
                        "Edit Final Report:",
                        value=st.session_state.hitl_final_output,
                        height=600,
                        key="edit_final_report"
                    )
                    st.session_state.hitl_edited_content = edited_report
                else:
                    # Display mode
                    st.markdown(st.session_state.hitl_final_output)
                
                # Final report review controls
                render_final_report_review_controls()
            else:
                # Display mode (not in review)
                st.markdown(st.session_state.hitl_final_output)
                
                # Option to start review
                if st.button("🔄 Review & Refine Final Report", type="primary"):
                    # Trigger final report review by getting state
                    state_data = get_workflow_state(st.session_state.hitl_thread_id)
                    if state_data:
                        st.session_state.hitl_final_output = state_data.get('state', {}).get('final_output', '')
                        st.rerun()
            
            # Download button
            st.download_button(
                "📥 Download Report",
                st.session_state.hitl_final_output,
                file_name="analysis_report.md",
                mime="text/markdown"
            )
        
        # Auto-refresh button
        if st.button("🔄 Refresh State"):
            state_data = get_workflow_state(st.session_state.hitl_thread_id)
            if state_data:
                state = state_data.get('state', {})
                st.session_state.hitl_plan = state.get('plan', [])
                st.session_state.hitl_sections = state.get('generated_sections', [])
                st.session_state.hitl_current_index = state.get('current_section_index', 0)
                st.session_state.hitl_data_profile = state.get('data_profile')
                st.session_state.hitl_final_output = state.get('final_output')
                
                # Update current section
                sections = st.session_state.hitl_sections
                current_idx = st.session_state.hitl_current_index
                current_step = state.get('current_step', '')
                
                # Check if we're in final report review mode
                if current_step == 'awaiting_final_report_review' or current_step == 'final_report_regenerated':
                    # In final report review, no current section
                    st.session_state.hitl_current_section = None
                elif sections and current_idx < len(sections):
                    st.session_state.hitl_current_section = sections[current_idx]
                
                st.success("State refreshed!")
                st.rerun()
    else:
        st.info("👆 Start a new analysis workflow above")

