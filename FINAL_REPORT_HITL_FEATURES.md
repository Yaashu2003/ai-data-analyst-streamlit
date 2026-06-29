# Final Report HITL Features

## Overview

After the report is generated, the HITL workflow now includes comprehensive review and refinement features, allowing users to edit, provide feedback, and regenerate the final compiled report.

## New Features

### 1. Final Report Review Node
- **Location**: After `finalize_node` completes
- **Purpose**: Pause workflow for human review of the complete report
- **State**: `awaiting_final_report_review`

### 2. Final Report Regeneration
- **Node**: `regenerate_final_report_node`
- **Capabilities**:
  - Incorporates general feedback
  - Applies direct text edits
  - Incorporates sentence-level feedback
  - Uses LLM to regenerate report with improvements

### 3. UI Features for Final Report

#### Edit Mode
- **Toggle**: "✏️ Toggle Edit Mode" button
- **Functionality**: Direct text editing of the entire final report
- **Storage**: Edits stored in `hitl_edited_content` session state

#### Review Controls
- **Approve**: "✅ Approve Final Report" - Finalizes and ends workflow
- **Compare Versions**: "🔄 Compare Versions" - Side-by-side original vs current
- **View Original**: "📋 View Original" - View the original generated report

#### Feedback Mechanisms
1. **General Feedback**:
   - Text area for overall feedback
   - Examples: "Make executive summary more concise", "Add more statistical details"

2. **Sentence-Level Feedback**:
   - Select specific text from report
   - Provide targeted feedback
   - Support multiple sentence feedbacks
   - Each feedback stored as `{text: str, feedback: str}`

3. **Direct Editing**:
   - Full text editor for the report
   - Changes applied directly or used as guidance for regeneration

#### Regeneration
- **Button**: "📤 Submit Feedback & Regenerate Report"
- **Process**:
  1. Collects all feedback (general, sentence-level, edits)
  2. Sends to backend
  3. LLM regenerates report incorporating feedback
  4. Returns to review state for further refinement

## Workflow Graph Updates

```
... (section generation flow) ...
  ↓
finalize
  ↓
final_report_review (INTERRUPT_BEFORE)
  ↓
[review_router]
  ├─→ "approved" → END
  └─→ "feedback" → regenerate_final_report
                      ↓
                  final_report_review (back to review)
```

## State Management

### New State Fields
- `final_output_original`: Stores the original generated report for comparison
- `final_output`: Current version of the report (may be edited/regenerated)

### State Transitions
1. `finalized` → Report compiled
2. `awaiting_final_report_review` → Paused for review
3. `final_report_regenerated` → Report regenerated, returns to review

## API Updates

### Streaming Events
- `final_report_review`: Pause point for final report review
  ```json
  {
    "type": "final_report_review",
    "data": {
      "final_output": "...",
      "final_output_original": "..."
    }
  }
  ```

- `final_report_regenerated`: Report has been regenerated
  ```json
  {
    "type": "final_report_regenerated",
    "data": {
      "final_output": "..."
    }
  }
  ```

### Resume Endpoint
The `/hitl/resume` endpoint now handles final report review:
- `review_action`: "approved" or "feedback"
- `human_comment`: General feedback
- `edited_content`: Direct edits
- `sentence_feedback`: Sentence-level feedbacks

## Usage Flow

1. **Report Generation**:
   - All sections approved
   - Final report compiled
   - Workflow pauses at `final_report_review`

2. **Review Phase**:
   - User views final report
   - Can toggle edit mode
   - Can provide feedback
   - Can compare versions

3. **Refinement** (Optional):
   - User submits feedback/edits
   - System regenerates report
   - Returns to review for approval

4. **Approval**:
   - User approves final report
   - Workflow ends
   - Report available for download

## UI Components

### Final Report Display
- Shows current version of report
- Edit mode toggle
- Version comparison view
- Original report viewer

### Review Controls Panel
- Approve button
- Compare versions toggle
- View original button
- Feedback input areas
- Regenerate button

### Feedback Collection
- General feedback text area
- Sentence selection and feedback
- Multiple sentence feedbacks support
- Direct edit mode

## Integration Points

### With Existing Workflow
- Seamlessly continues from section-by-section review
- Uses same feedback mechanisms
- Maintains state consistency

### With LLM
- Uses Groq/Llama for report regeneration
- Incorporates all feedback types
- Maintains report structure

## Example Use Cases

1. **Concise Summary Request**:
   - Feedback: "Make the executive summary more concise, focus on top 3 insights"
   - Result: Regenerated report with shorter, focused summary

2. **Statistical Details**:
   - Feedback: "Add more statistical details and numbers to support conclusions"
   - Result: Report includes more quantitative analysis

3. **Sentence-Level Refinement**:
   - Select: "Sales increased significantly"
   - Feedback: "Specify the percentage increase"
   - Result: Sentence updated to "Sales increased by 23%"

4. **Direct Editing**:
   - Edit mode: Directly modify report text
   - Submit: Changes applied or used as guidance for regeneration

## Benefits

1. **Iterative Refinement**: Multiple rounds of feedback and regeneration
2. **Precise Control**: Sentence-level feedback for targeted improvements
3. **Flexibility**: Direct editing or LLM-assisted regeneration
4. **Transparency**: Version comparison to see changes
5. **Quality Assurance**: Review before final approval

## Technical Notes

- Original report stored for comparison
- State persists through regeneration cycles
- LLM regeneration maintains structure
- All feedback types combined for comprehensive improvement
- Workflow can loop through multiple refinement cycles

