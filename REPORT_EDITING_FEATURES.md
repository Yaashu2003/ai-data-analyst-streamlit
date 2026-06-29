# Report Editing & Feedback Features

## Overview

The Report Viewer now includes comprehensive editing, feedback, and comparison features. When you click "Show Report", you can edit the text, provide feedback on specific sentences, and generate a final improved version that can be compared with the original.

## Features

### 1. Three-Tab Interface

#### Tab 1: 📄 View Report
- Original HTML report display
- Interactive charts and visualizations
- Download original report

#### Tab 2: ✏️ Edit & Feedback
- **Editable Text View**: Direct text editing of the report
- **Preview Mode**: Markdown preview of the report
- **Editing Tools**: Toggle edit mode, compare versions, view original
- **Feedback Collection**: General and sentence-level feedback
- **Generate Final Report**: Create improved version with all feedback

#### Tab 3: 📊 Final Report
- View the generated final report
- Compare with original
- Download final report

### 2. Text Editing

#### Direct Editing
- Full text area for editing the entire report
- Real-time editing
- Changes saved in session state
- Toggle between editable text and preview modes

#### Edit Mode Toggle
- Button to switch edit mode on/off
- Preserves edits when toggling

### 3. Feedback Mechanisms

#### General Feedback
- Text area for overall report feedback
- Examples:
  - "Make the executive summary more concise"
  - "Add more statistical details"
  - "Improve the insights section"

#### Sentence-Level Feedback
- **Text Selection Helper**: Instructions and helper component
- **Process**:
  1. Select text in the report (click and drag)
  2. Copy selected text (Ctrl+C / Cmd+C)
  3. Paste in "Selected text from report" field
  4. Provide feedback for that specific text
  5. Click "Add Sentence Feedback"
- **Multiple Feedbacks**: Support for multiple sentence-level feedbacks
- **Management**: View and remove individual feedbacks

### 4. Final Report Generation

#### Process
1. Collect all inputs:
   - Direct text edits (if any)
   - General feedback
   - Sentence-level feedbacks
2. Send to LLM (Groq/Llama) for regeneration
3. Generate improved version incorporating all feedback
4. Store in session state

#### LLM Integration
- Uses Groq API with Llama 3.3 70B model
- Incorporates:
  - User edits
  - General feedback
  - Sentence-level feedbacks
- Maintains report structure
- Improves clarity and completeness

### 5. Comparison Features

#### Version Comparison
- **Toggle**: "🔄 Compare Versions" button
- **Side-by-Side View**:
  - Left: Original Report
  - Right: Final/Edited Report
- **View Original**: Button to view original report in expander

#### Final Report Comparison
- Compare final generated report with original
- Available in Final Report tab
- Side-by-side display

## User Workflow

### Step 1: Load Report
1. Click "✅ Show Report"
2. Report loads and text is extracted
3. Original text stored in session state

### Step 2: Review & Edit
1. Navigate to "✏️ Edit & Feedback" tab
2. View report in editable text or preview mode
3. Optionally edit text directly
4. Optionally provide general feedback

### Step 3: Sentence-Level Feedback (Optional)
1. Select text in the report (or from editable view)
2. Copy selected text (Ctrl+C)
3. Paste in "Selected text from report" field
4. Provide specific feedback
5. Click "Add Sentence Feedback"
6. Repeat for multiple sentences

### Step 4: Generate Final Report
1. Click "🚀 Generate Final Report"
2. System processes:
   - Direct edits
   - General feedback
   - Sentence-level feedbacks
3. LLM generates improved version
4. Final report displayed in "📊 Final Report" tab

### Step 5: Review & Compare
1. View final report in "📊 Final Report" tab
2. Compare with original using comparison view
3. Download final report if satisfied
4. Or return to editing for further refinement

## Technical Implementation

### Text Extraction
- Uses BeautifulSoup to parse HTML
- Removes script and style elements
- Extracts readable text content
- Cleans whitespace and formatting

### State Management
- `report_original_text`: Original extracted text
- `report_edited_text`: User-edited version
- `report_final_text`: LLM-generated final version
- `report_edit_mode`: Edit mode toggle state
- `report_sentence_feedbacks`: List of sentence feedbacks
- `report_show_comparison`: Comparison view toggle
- `report_general_feedback`: General feedback text

### LLM Prompt
The final report generation uses a comprehensive prompt that:
- Includes original report
- Incorporates user edits
- Addresses all feedback points
- Maintains structure
- Improves clarity and accuracy

### Text Selection Helper
- HTML component with JavaScript
- Visual instructions
- Paste event handling
- Selection highlighting

## Benefits

1. **Iterative Refinement**: Multiple rounds of editing and feedback
2. **Precise Control**: Sentence-level feedback for targeted improvements
3. **Flexibility**: Direct editing or LLM-assisted generation
4. **Transparency**: Version comparison to see all changes
5. **Quality Assurance**: Review before finalizing
6. **User-Friendly**: Simple text selection and feedback process

## Example Use Cases

### Use Case 1: Concise Summary
- **General Feedback**: "Make the executive summary more concise, focus on top 3 insights"
- **Result**: Final report with shorter, focused summary

### Use Case 2: Statistical Details
- **General Feedback**: "Add more statistical details and numbers to support conclusions"
- **Result**: Report includes more quantitative analysis

### Use Case 3: Sentence Refinement
- **Selected Text**: "Sales increased significantly"
- **Feedback**: "Specify the percentage increase"
- **Result**: Sentence updated to "Sales increased by 23%"

### Use Case 4: Direct Editing
- **Action**: Directly edit report text
- **Result**: Edits preserved and incorporated into final version

### Use Case 5: Combined Approach
- **Direct Edits**: Fix typos and formatting
- **General Feedback**: "Improve insights section"
- **Sentence Feedback**: Multiple targeted improvements
- **Result**: Comprehensive improvement incorporating all inputs

## Integration

### With Existing System
- Works with existing HTML report generation
- No changes to report generation process
- Enhances report viewing experience
- Maintains backward compatibility

### Dependencies
- BeautifulSoup: HTML parsing
- LangChain Groq: LLM integration
- Streamlit: UI components
- Standard library: Text processing

## Future Enhancements

- [ ] Real-time collaborative editing
- [ ] Version history tracking
- [ ] Export to multiple formats (PDF, Word, etc.)
- [ ] Advanced diff highlighting
- [ ] AI suggestions for improvements
- [ ] Batch feedback processing
- [ ] Template-based regeneration

