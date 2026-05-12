from typing import TypedDict, List, Dict, Any, Optional, Tuple
import json

def summarize_for_llm(data, max_items=10, current_depth=0, max_depth=3):
    if current_depth > max_depth:
        return f"... truncated at depth {max_depth}"
    
    if isinstance(data, dict):
        keys = list(data.keys())
        if len(keys) > max_items:
            keys = keys[:max_items]
            summary = {k: summarize_for_llm(data[k], max_items, current_depth+1, max_depth) for k in keys}
            summary['_summary'] = f"... {len(data) - max_items} more keys truncated"
            return summary
        else:
            return {k: summarize_for_llm(v, max_items, current_depth+1, max_depth) for k,v in data.items()}
    
    elif isinstance(data, list):
        if len(data) > max_items:
            summary = [summarize_for_llm(i, max_items, current_depth+1, max_depth) for i in data[:max_items]]
            summary.append(f"... {len(data) - max_items} more items truncated")
            return summary
        else:
            return [summarize_for_llm(i, max_items, current_depth+1, max_depth) for i in data]
    
    else:
        return data

def extract_json_from_response(response_content: str) -> Optional[Dict[str, Any]]:
    """Extract JSON from LLM response that may contain markdown code blocks"""
    try:
        # Try direct JSON parsing first
        return json.loads(response_content.strip())
    except json.JSONDecodeError:
        try:
            # Extract JSON from markdown code blocks
            import re
            
            # Look for ```json...``` or ```...``` blocks
            json_pattern = r'```(?:json)?\s*\n(.*?)\n```'
            matches = re.findall(json_pattern, response_content, re.DOTALL)
            
            if matches:
                json_str = matches[0].strip()
                return json.loads(json_str)
            
            # If no code blocks, try to find JSON-like content
            # Look for content between { and }
            start = response_content.find('{')
            end = response_content.rfind('}') + 1
            
            if start != -1 and end > start:
                json_str = response_content[start:end]
                return json.loads(json_str)
                
        except (json.JSONDecodeError, IndexError):
            pass
    
    return None
# utils.py (ADD THIS NEW FUNCTION)
