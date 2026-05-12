"""
Reusable Sentence Selection & Feedback Popup Component for Streamlit

This module provides a reusable component for text selection and feedback collection
that can be integrated into any Streamlit application.

Usage:
    from sentence_feedback_component import render_sentence_feedback_component
    
    # In your Streamlit app
    render_sentence_feedback_component(
        enabled=True,
        target_selector=".selectable-content",
        session_key="my_feedback_key",
        placeholder="Provide feedback for this selection...",
        popup_position="bottom"  # or "center"
    )
    
    # Access collected feedbacks
    if 'my_feedback_key' in st.session_state:
        feedbacks = st.session_state.my_feedback_key
        for fb in feedbacks:
            print(f"Text: {fb['text']}, Feedback: {fb['feedback']}")
"""

import streamlit as st
import json
from typing import List, Dict, Optional, Literal


def render_sentence_feedback_component(
    enabled: bool = True,
    target_selector: str = ".selectable-content",
    session_key: str = "sentence_feedbacks",
    placeholder: str = "Enter your feedback or comments here. You can add multiple comments separated by new lines or as separate feedbacks.",
    popup_position: Literal["bottom", "center"] = "bottom",
    primary_color: str = "#10b981",
    border_color: str = "#10b981",
    min_selection_length: int = 1,
    max_selection_length: int = 500,
    show_feedback_history: bool = True,
    feedback_history_title: str = "Saved Feedbacks"
) -> List[Dict[str, str]]:
    """
    Render a reusable sentence feedback component.
    
    Args:
        enabled: Whether the component is enabled
        target_selector: CSS selector for selectable content areas
        session_key: Session state key to store feedbacks
        placeholder: Placeholder text for feedback input
        popup_position: Position of popup ("bottom" or "center")
        primary_color: Primary color for buttons and borders
        border_color: Border color for popup
        min_selection_length: Minimum length of selected text
        max_selection_length: Maximum length of selected text
        show_feedback_history: Whether to display feedback history
        feedback_history_title: Title for feedback history section
    
    Returns:
        List of feedback dictionaries with 'text', 'feedback', and 'id' keys
    """
    # Initialize session state
    if session_key not in st.session_state:
        st.session_state[session_key] = []
    
    # Generate unique component ID based on session key
    component_id = session_key.replace(" ", "_").lower()
    
    # CSS Styles
    css_styles = f"""
    <style>
    /* Sentence Feedback Popup */
    .sentence-feedback-popup-{component_id} {{
        position: fixed;
        {'bottom: 20px; left: 50%; transform: translateX(-50%);' if popup_position == 'bottom' else 'left: 50%; top: 50%; transform: translate(-50%, -50%);'}
        background: #ffffff;
        border: 2px solid {border_color};
        border-radius: 12px;
        padding: 1rem;
        box-shadow: 0 10px 25px rgba(0, 0, 0, 0.15);
        z-index: 10000;
        min-width: 300px;
        max-width: 500px;
        width: calc(100% - 40px);
        animation: slideUp-{component_id} 0.3s ease-out;
        pointer-events: auto;
    }}
    
    @keyframes slideUp-{component_id} {{
        from {{
            opacity: 0;
            {'transform: translateX(-50%) translateY(20px);' if popup_position == 'bottom' else 'transform: translate(-50%, -50%) translateY(20px);'}
        }}
        to {{
            opacity: 1;
            {'transform: translateX(-50%) translateY(0);' if popup_position == 'bottom' else 'transform: translate(-50%, -50%) translateY(0);'}
        }}
    }}
    
    .sentence-feedback-overlay-{component_id} {{
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: rgba(0, 0, 0, 0.5);
        z-index: 9999;
        display: none;
    }}
    
    .sentence-feedback-header-{component_id} {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 0.75rem;
        font-size: 0.85rem;
        color: #6b7280;
    }}
    
    .sentence-feedback-close-{component_id} {{
        background: none;
        border: none;
        color: #6b7280;
        cursor: pointer;
        font-size: 1.5rem;
        line-height: 1;
        padding: 0;
        width: 24px;
        height: 24px;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: color 0.2s;
        border-radius: 4px;
    }}
    
    .sentence-feedback-close-{component_id}:hover {{
        color: #111827;
        background: #f3f4f6;
    }}
    
    .sentence-feedback-input-{component_id} {{
        width: 100%;
        padding: 0.75rem;
        border: 1px solid #d1d5db;
        border-radius: 8px;
        background: #f9fafb;
        color: #111827;
        font-size: 0.9rem;
        resize: vertical;
        margin-bottom: 0.75rem;
        font-family: inherit;
        outline: none;
        transition: border-color 0.2s;
        box-sizing: border-box;
    }}
    
    .sentence-feedback-input-{component_id}:focus {{
        border-color: {primary_color};
        background: #ffffff;
    }}
    
    .sentence-feedback-submit-{component_id} {{
        background: {primary_color};
        color: white;
        border: none;
        padding: 0.5rem 1rem;
        border-radius: 8px;
        font-weight: 600;
        cursor: pointer;
        font-size: 0.9rem;
        transition: all 0.2s;
        width: 100%;
    }}
    
    .sentence-feedback-submit-{component_id}:hover:not(:disabled) {{
        background: #059669;
        opacity: 0.9;
    }}
    
    .sentence-feedback-submit-{component_id}:disabled {{
        background: #d1d5db;
        color: #6b7280;
        cursor: not-allowed;
        opacity: 0.6;
    }}
    
    .sentence-feedback-selected-text-{component_id} {{
        background: #f5f5f5;
        padding: 10px;
        border-radius: 5px;
        margin: 10px 0;
        font-style: italic;
        color: #666;
        border-left: 3px solid {primary_color};
        max-height: 100px;
        overflow-y: auto;
        word-wrap: break-word;
        font-size: 0.85rem;
    }}
    
    /* All Sentence Feedbacks Container */
    .all-sentence-feedbacks-{component_id} {{
        margin-bottom: 1rem;
        margin-top: 1rem;
    }}
    
    .all-sentence-feedbacks-{component_id} .sentence-feedback-label {{
        font-size: 0.9rem;
        font-weight: 600;
        color: #92400e;
        margin-bottom: 0.75rem;
    }}
    
    /* Sentence Feedback Display */
    .sentence-feedback-display-{component_id} {{
        background: #fef3c7;
        border: 1px solid #fbbf24;
        border-radius: 8px;
        padding: 1rem;
        margin-bottom: 0.75rem;
        position: relative;
    }}
    
    .sentence-feedback-text-{component_id} {{
        font-size: 0.9rem;
        color: #78350f;
        margin-bottom: 0.5rem;
        padding-right: 2rem;
    }}
    
    .sentence-feedback-remove-{component_id} {{
        position: absolute;
        top: 0.5rem;
        right: 0.5rem;
        background: none;
        border: none;
        color: #92400e;
        cursor: pointer;
        font-size: 0.9rem;
        padding: 0.25rem 0.5rem;
        border-radius: 4px;
        transition: all 0.2s;
    }}
    
    .sentence-feedback-remove-{component_id}:hover {{
        background: #fbbf24;
        color: #78350f;
    }}
    
    /* Make content selectable */
    {target_selector} {{
        user-select: text;
        -webkit-user-select: text;
        -moz-user-select: text;
        -ms-user-select: text;
    }}
    
    /* Responsive */
    @media (max-width: 768px) {{
        .sentence-feedback-popup-{component_id} {{
            min-width: 250px;
            max-width: calc(100% - 20px);
            width: calc(100% - 20px);
            {'bottom: 10px; left: 10px; transform: none;' if popup_position == 'bottom' else 'left: 50%; top: 50%; transform: translate(-50%, -50%);'}
        }}
    }}
    </style>
    """
    
    # JavaScript Component
    js_component = f"""
    <script>
    (function() {{
        'use strict';
        
        const COMPONENT_ID = '{component_id}';
        const TARGET_SELECTOR = '{target_selector}';
        const MIN_LENGTH = {min_selection_length};
        const MAX_LENGTH = {max_selection_length};
        const SESSION_KEY = '{session_key}';
        
        let selectedText = '';
        let modal = null;
        let overlay = null;
        let selectionTimeout = null;
        let justSelected = false;
        
        function createModal() {{
            if (modal) return;
            
            const modalHTML = `
                <div id="feedback-overlay-${{COMPONENT_ID}}" class="sentence-feedback-overlay-${{COMPONENT_ID}}"></div>
                <div id="feedback-modal-${{COMPONENT_ID}}" class="sentence-feedback-popup-${{COMPONENT_ID}}" style="display: none;">
                    <div class="sentence-feedback-header-${{COMPONENT_ID}}">
                        <span>📝 Feedback for: "<span id="selected-text-preview-${{COMPONENT_ID}}"></span>"</span>
                        <button class="sentence-feedback-close-${{COMPONENT_ID}}" id="close-btn-${{COMPONENT_ID}}">×</button>
                    </div>
                    <div>
                        <strong>Selected Text:</strong>
                        <div id="selected-text-display-${{COMPONENT_ID}}" class="sentence-feedback-selected-text-${{COMPONENT_ID}}"></div>
                    </div>
                    <div>
                        <strong>Your Feedback/Comments:</strong>
                        <textarea id="feedback-input-${{COMPONENT_ID}}" class="sentence-feedback-input-${{COMPONENT_ID}}" rows="4" placeholder="{placeholder}"></textarea>
                        <small style="color: #666; font-size: 12px;">💡 Tip: You can add multiple feedbacks for the same text by submitting multiple times.</small>
                    </div>
                    <button id="submit-btn-${{COMPONENT_ID}}" class="sentence-feedback-submit-${{COMPONENT_ID}}">Save Feedback</button>
                </div>
            `;
            
            document.body.insertAdjacentHTML('beforeend', modalHTML);
            modal = document.getElementById(`feedback-modal-${{COMPONENT_ID}}`);
            overlay = document.getElementById(`feedback-overlay-${{COMPONENT_ID}}`);
            
            const submitBtn = document.getElementById(`submit-btn-${{COMPONENT_ID}}`);
            const closeBtn = document.getElementById(`close-btn-${{COMPONENT_ID}}`);
            const feedbackInput = document.getElementById(`feedback-input-${{COMPONENT_ID}}`);
            
            submitBtn.onclick = handleSubmit;
            closeBtn.onclick = handleClose;
            overlay.onclick = handleClose;
            
            // Update submit button state
            feedbackInput.addEventListener('input', function() {{
                const hasText = this.value.trim().length > 0;
                submitBtn.textContent = hasText ? 'Save Feedback' : 'Close';
                submitBtn.disabled = !hasText;
            }});
            
            // Escape key to close
            document.addEventListener('keydown', function(e) {{
                if (e.key === 'Escape' && modal && modal.style.display === 'block') {{
                    handleClose();
                }}
            }});
        }}
        
        function showModal(text) {{
            if (!text || text.length < MIN_LENGTH || text.length > MAX_LENGTH) return;
            
            createModal();
            selectedText = text;
            
            const previewEl = document.getElementById(`selected-text-preview-${{COMPONENT_ID}}`);
            const displayEl = document.getElementById(`selected-text-display-${{COMPONENT_ID}}`);
            
            if (previewEl) previewEl.textContent = text.length > 30 ? text.substring(0, 30) + '...' : text;
            if (displayEl) displayEl.textContent = text;
            
            modal.style.display = 'block';
            overlay.style.display = 'block';
            
            setTimeout(() => {{
                const input = document.getElementById(`feedback-input-${{COMPONENT_ID}}`);
                if (input) input.focus();
            }}, 100);
        }}
        
        function handleClose() {{
            if (modal) {{
                modal.style.display = 'none';
                overlay.style.display = 'none';
                const input = document.getElementById(`feedback-input-${{COMPONENT_ID}}`);
                if (input) input.value = '';
                selectedText = '';
                if (window.getSelection) window.getSelection().removeAllRanges();
            }}
        }}
        
        function handleSubmit() {{
            const input = document.getElementById(`feedback-input-${{COMPONENT_ID}}`);
            const feedback = input ? input.value.trim() : '';
            
            if (!feedback) {{
                handleClose();
                return;
            }}
            
            if (!selectedText) return;
            
            // Store in sessionStorage for Streamlit to process
            const feedbackData = {{
                selectedText: selectedText,
                feedback: feedback,
                timestamp: Date.now(),
                componentId: COMPONENT_ID,
                sessionKey: SESSION_KEY
            }};
            
            sessionStorage.setItem(`pendingFeedback_${{COMPONENT_ID}}`, JSON.stringify(feedbackData));
            
            handleClose();
            
            // Trigger Streamlit rerun
            const event = new CustomEvent('feedbackSubmitted', {{ detail: feedbackData }});
            window.dispatchEvent(event);
            
            // Show success notification
            const notification = document.createElement('div');
            notification.textContent = '✅ Feedback saved! You can select more text to add additional feedbacks.';
            notification.style.cssText = 'position: fixed; top: 20px; right: 20px; background: #10b981; color: white; padding: 12px 20px; border-radius: 8px; z-index: 10001; box-shadow: 0 4px 12px rgba(0,0,0,0.15); animation: slideIn 0.3s ease-out;';
            document.body.appendChild(notification);
            setTimeout(() => {{
                notification.style.animation = 'fadeOut 0.3s ease-out';
                setTimeout(() => notification.remove(), 300);
            }}, 2000);
        }}
        
        function handleSelection() {{
            // Don't process if clicking inside the popup
            const popup = document.querySelector(`.sentence-feedback-popup-${{COMPONENT_ID}}`);
            if (popup && popup.style.display === 'block') {{
                const activeElement = document.activeElement;
                if (popup.contains(activeElement) || activeElement?.closest(`.sentence-feedback-popup-${{COMPONENT_ID}}`)) {{
                    return;
                }}
            }}
            
            // Clear any existing timeout
            if (selectionTimeout) {{
                clearTimeout(selectionTimeout);
            }}
            
            const selection = window.getSelection();
            const selectedText = selection.toString().trim();
            
            if (selectedText && selectedText.length >= MIN_LENGTH && selectedText.length <= MAX_LENGTH) {{
                // Check if selection is within target area
                const targetContainers = document.querySelectorAll(TARGET_SELECTOR);
                let isInTarget = false;
                
                // Also check textareas and contenteditable elements
                const textAreas = document.querySelectorAll('textarea');
                const allTargets = [...targetContainers, ...textAreas];
                
                for (const container of allTargets) {{
                    if (selection.anchorNode && container.contains(selection.anchorNode)) {{
                        isInTarget = true;
                        break;
                    }}
                }}
                
                if (isInTarget) {{
                    // Use a small delay to prevent immediate closing
                    selectionTimeout = setTimeout(() => {{
                        showModal(selectedText);
                        justSelected = true;
                        
                        // Reset flag after a short delay
                        setTimeout(() => {{
                            justSelected = false;
                        }}, 300);
                    }}, 150);
                }} else if (!justSelected && !modal?.style.display || modal?.style.display === 'none') {{
                    // Clear selection if not in target area
                    if (window.getSelection) window.getSelection().removeAllRanges();
                }}
            }} else if (!justSelected && (!modal || modal.style.display === 'none')) {{
                // Clear if no selection
                if (window.getSelection) window.getSelection().removeAllRanges();
            }}
        }}
        
        function clickOutsideHandler(e) {{
            // Don't close if clicking inside the popup or if we just selected
            if (justSelected) return;
            
            if (modal && modal.style.display === 'block') {{
                const isClickInPopup = modal.contains(e.target) ||
                                      e.target.closest(`.sentence-feedback-popup-${{COMPONENT_ID}}`) ||
                                      e.target.classList?.contains(`sentence-feedback-input-${{COMPONENT_ID}}`) ||
                                      e.target.classList?.contains(`sentence-feedback-submit-${{COMPONENT_ID}}`) ||
                                      e.target.classList?.contains(`sentence-feedback-close-${{COMPONENT_ID}}`);
                
                if (!isClickInPopup) {{
                    handleClose();
                }}
            }}
        }}
        
        // Initialize if enabled
        if ({str(enabled).lower()}) {{
            document.addEventListener('mouseup', handleSelection);
            document.addEventListener('keyup', handleSelection);
            document.addEventListener('click', clickOutsideHandler, true);
        }}
        
        // Add animation styles
        const style = document.createElement('style');
        style.textContent = `
            @keyframes slideIn {{
                from {{ transform: translateX(100%); opacity: 0; }}
                to {{ transform: translateX(0); opacity: 1; }}
            }}
            @keyframes fadeOut {{
                from {{ opacity: 1; }}
                to {{ opacity: 0; }}
            }}
        `;
        document.head.appendChild(style);
    }})();
    </script>
    """
    
    # Render CSS and JS
    st.markdown(css_styles, unsafe_allow_html=True)
    st.markdown(js_component, unsafe_allow_html=True)
    
    # Process pending feedback from sessionStorage
    feedback_key = f"feedback_session_{component_id}"
    if feedback_key not in st.session_state:
        st.session_state[feedback_key] = 0
    
    # Add script to check for pending feedback and update Streamlit
    feedback_processor_script = f"""
    <script>
    (function() {{
        const COMPONENT_ID = '{component_id}';
        const SESSION_KEY = '{session_key}';
        
        function processPendingFeedback() {{
            const pendingKey = `pendingFeedback_${{COMPONENT_ID}}`;
            const pending = sessionStorage.getItem(pendingKey);
            
            if (pending) {{
                try {{
                    const data = JSON.parse(pending);
                    sessionStorage.removeItem(pendingKey);
                    
                    // Store in a way Streamlit can access
                    const hiddenInput = document.querySelector(`input[key='feedback_input_${{COMPONENT_ID}}']`);
                    if (hiddenInput) {{
                        hiddenInput.value = pending;
                        hiddenInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        // Trigger Streamlit update
                        if (window.parent && window.parent.postMessage) {{
                            window.parent.postMessage({{type: 'streamlit:setComponentValue', value: pending}}, '*');
                        }}
                    }}
                }} catch (e) {{
                    console.error('Error processing feedback:', e);
                }}
            }}
        }}
        
        // Check periodically for pending feedback
        setInterval(processPendingFeedback, 500);
        
        // Also listen for the custom event
        window.addEventListener('feedbackSubmitted', function(e) {{
            setTimeout(processPendingFeedback, 100);
        }});
    }})();
    </script>
    """
    st.markdown(feedback_processor_script, unsafe_allow_html=True)
    
    # Hidden input to trigger rerun when feedback is submitted
    feedback_data = st.text_input(
        f"Feedback Data Input ({component_id})",
        value="",
        key=f"feedback_input_{component_id}",
        label_visibility="collapsed"
    )
    
    # Process feedback from JavaScript
    if feedback_data:
        try:
            data = json.loads(feedback_data)
            if 'selectedText' in data and 'feedback' in data:
                # Check if this feedback is already added (avoid duplicates)
                is_duplicate = any(
                    sf.get('text') == data['selectedText'] and sf.get('feedback') == data['feedback']
                    for sf in st.session_state[session_key]
                )
                
                if not is_duplicate:
                    new_feedback = {
                        "text": data['selectedText'],
                        "feedback": data['feedback'],
                        "id": data.get('timestamp', len(st.session_state[session_key])),
                        "timestamp": data.get('timestamp', 0)
                    }
                    st.session_state[session_key].append(new_feedback)
                    st.session_state[feedback_key] += 1
                    # Clear the input to allow new feedbacks
                    st.session_state[f"feedback_input_{component_id}"] = ""
                    st.rerun()
        except Exception as e:
            pass
    
    # Display feedback history if enabled
    if show_feedback_history and st.session_state[session_key]:
        with st.expander(f"📋 {feedback_history_title} ({len(st.session_state[session_key])})", expanded=False):
            for i, fb in enumerate(st.session_state[session_key]):
                col1, col2 = st.columns([4, 1])
                with col1:
                    st.markdown(f"**#{i+1}:** \"{fb['text'][:60]}...\" → {fb['feedback']}")
                with col2:
                    if st.button("🗑️", key=f"remove_fb_{component_id}_{i}"):
                        st.session_state[session_key].pop(i)
                        st.rerun()
    
    # Return collected feedbacks
    return st.session_state[session_key]
