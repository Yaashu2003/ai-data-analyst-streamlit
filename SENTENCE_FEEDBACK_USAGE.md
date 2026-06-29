# Sentence Feedback Component - Usage Guide

## Overview

The `sentence_feedback_component.py` provides a reusable, React-inspired sentence selection and feedback popup component for Streamlit applications. It automatically detects text selection and opens a popup for feedback collection.

## Features

✅ **Automatic text selection detection**  
✅ **Popup appears on selection**  
✅ **Click-outside to close**  
✅ **Prevents event bubbling**  
✅ **Responsive design**  
✅ **Optional feedback history display**  
✅ **Customizable target areas via CSS selector**  
✅ **Enable/disable toggle**  
✅ **Multiple feedbacks support**  
✅ **Beautiful animations and styling**

## Basic Usage

```python
import streamlit as st
from sentence_feedback_component import render_sentence_feedback_component

# Render the component
feedbacks = render_sentence_feedback_component(
    enabled=True,
    target_selector=".selectable-content",
    session_key="my_feedbacks",
    placeholder="Enter your feedback here..."
)

# Access collected feedbacks
if feedbacks:
    st.write(f"Collected {len(feedbacks)} feedback(s)")
    for fb in feedbacks:
        st.write(f"Text: {fb['text']}")
        st.write(f"Feedback: {fb['feedback']}")
```

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enabled` | bool | `True` | Whether the component is enabled |
| `target_selector` | str | `".selectable-content"` | CSS selector for selectable content areas |
| `session_key` | str | `"sentence_feedbacks"` | Session state key to store feedbacks |
| `placeholder` | str | `"Enter your feedback..."` | Placeholder text for feedback input |
| `popup_position` | str | `"bottom"` | Position of popup (`"bottom"` or `"center"`) |
| `primary_color` | str | `"#10b981"` | Primary color for buttons and borders |
| `border_color` | str | `"#10b981"` | Border color for popup |
| `min_selection_length` | int | `1` | Minimum length of selected text |
| `max_selection_length` | int | `500` | Maximum length of selected text |
| `show_feedback_history` | bool | `True` | Whether to display feedback history |
| `feedback_history_title` | str | `"Saved Feedbacks"` | Title for feedback history section |

## Complete Example

```python
import streamlit as st
from sentence_feedback_component import render_sentence_feedback_component

st.title("My App with Sentence Feedback")

# Your content area - make sure it has the target selector class
st.markdown('<div class="selectable-content">', unsafe_allow_html=True)

st.markdown("""
## Your Content Here

Select any text in this area to provide feedback. 
The popup will appear automatically when you select text.

This is another paragraph. Try selecting different sentences 
to see how the feedback system works.
""")

st.markdown('</div>', unsafe_allow_html=True)

# Add the SentenceFeedback component
feedbacks = render_sentence_feedback_component(
    enabled=True,
    target_selector=".selectable-content",
    session_key="my_feedbacks",
    placeholder="E.g., Make this more concise, add more detail...",
    popup_position="bottom",
    primary_color="#3f51b5",
    show_feedback_history=True
)

# Display collected feedbacks
if feedbacks:
    st.markdown("### Collected Feedbacks:")
    for i, fb in enumerate(feedbacks, 1):
        with st.expander(f"Feedback #{i}"):
            st.write(f"**Selected Text:** \"{fb['text']}\"")
            st.write(f"**Feedback:** {fb['feedback']}")
```

## Integration with Text Areas

The component automatically works with Streamlit text areas:

```python
import streamlit as st
from sentence_feedback_component import render_sentence_feedback_component

# Create a text area
text_content = st.text_area(
    "Your Content:",
    value="Select text in this area to provide feedback...",
    height=400,
    key="my_text_area"
)

# Render component - it will detect selections in the textarea
render_sentence_feedback_component(
    enabled=True,
    target_selector="textarea[key='my_text_area']",
    session_key="text_area_feedbacks"
)
```

## Customization

### Custom Colors

```python
render_sentence_feedback_component(
    primary_color="#ff6b6b",  # Red theme
    border_color="#ff6b6b",
    target_selector=".my-content"
)
```

### Center Position Popup

```python
render_sentence_feedback_component(
    popup_position="center",  # Instead of "bottom"
    target_selector=".my-content"
)
```

### Hide Feedback History

```python
render_sentence_feedback_component(
    show_feedback_history=False,  # Don't show history
    target_selector=".my-content"
)
```

## Advanced Usage

### Multiple Components

You can use multiple components with different session keys:

```python
# Component 1
feedbacks1 = render_sentence_feedback_component(
    session_key="section1_feedbacks",
    target_selector=".section1"
)

# Component 2
feedbacks2 = render_sentence_feedback_component(
    session_key="section2_feedbacks",
    target_selector=".section2"
)
```

### Conditional Rendering

```python
if st.checkbox("Enable Feedback"):
    render_sentence_feedback_component(
        enabled=True,
        target_selector=".content"
    )
else:
    render_sentence_feedback_component(
        enabled=False,
        target_selector=".content"
    )
```

## Data Structure

Each feedback is stored as a dictionary:

```python
{
    "text": "The selected text",
    "feedback": "User's feedback comment",
    "id": 1234567890,  # Timestamp-based unique ID
    "timestamp": 1234567890  # Unix timestamp
}
```

## Accessing Feedbacks

Feedbacks are stored in `st.session_state` under the `session_key`:

```python
# Get feedbacks directly
feedbacks = st.session_state.get("my_feedbacks", [])

# Or use the return value
feedbacks = render_sentence_feedback_component(session_key="my_feedbacks")

# Process feedbacks
for fb in feedbacks:
    print(f"Text: {fb['text']}")
    print(f"Feedback: {fb['feedback']}")
    print(f"Timestamp: {fb['timestamp']}")
```

## Tips

1. **Target Selector**: Make sure your content has the CSS class or selector you specify in `target_selector`
2. **Session Key**: Use unique session keys if you have multiple components
3. **Text Length**: Adjust `min_selection_length` and `max_selection_length` based on your needs
4. **Styling**: The component uses CSS that won't conflict with Streamlit's default styles
5. **Mobile**: The component is responsive and works on mobile devices

## Troubleshooting

### Popup doesn't appear
- Check that `enabled=True`
- Verify the `target_selector` matches your content
- Ensure text selection is within the target area

### Feedbacks not saving
- Check that `session_key` is unique
- Verify Streamlit session state is working
- Check browser console for JavaScript errors

### Styling conflicts
- The component uses scoped CSS classes
- Each component instance has a unique ID
- Styles won't conflict with other components

## Example: Report Viewer Integration

See `report_viewer.py` for a complete integration example where the component is used for:
- Text area editing mode
- Markdown preview mode
- Multiple feedback collection
- Integration with report regeneration
