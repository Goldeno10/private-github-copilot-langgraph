import json
import uuid
import re
from fastapi import FastAPI, Request # type: ignore
from fastapi.responses import StreamingResponse # type: ignore
from langchain_core.messages import HumanMessage # type: ignore
from .graph import app as graph_app


app = FastAPI()

def extract_user_intent(body: dict) -> str:
    """Extracts the actual user instruction from Continue's verbose wrapper."""
    # Case 1: Standard Chat List
    messages = body.get("messages")
    if isinstance(messages, list) and messages:
        return messages[-1].get("content", "")
    
    # Case 2: /api/generate prompt (Continue's inline edit wrapper)
    prompt = body.get("prompt", "")
    if isinstance(prompt, str) and "The user's request is:" in prompt:
        # Regex to grab what's between "The user's request is: '" and "'"
        match = re.search(r"The user's request is: \"(.*?)\"", prompt)
        if match:
            return match.group(1)
        # Fallback for different quote styles
        match = re.search(r"The user's request is: '(.*?)'", prompt)
        return match.group(1) if match else prompt

    # Case 3: Raw string
    return str(body.get("prompt", body))


@app.post("/api/chat")
@app.post("/api/generate") # Route both to the same logic
async def chat_endpoint(request: Request):
    body = await request.json()
    user_input = extract_user_intent(body)
    
    # print(f"=== Extracted Intent: {user_input} ===")
    
    async def stream_copilot_response():
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}
        
        try:
            async for event in graph_app.astream_events(
                {"messages": [HumanMessage(content=user_input)]},
                config=config,
                version="v2"
            ):
                kind = event["event"]
                
                # Stream raw LLM tokens
                if kind == "on_chat_model_stream":
                    content = event["data"]["chunk"].content
                    if content:
                        yield json.dumps({
                            "model": "gpt-oss:120b-cloud",
                            "message": {"role": "assistant", "content": content},
                            "done": False
                        }) + "\n"

                # Stream Tool Success Messages (e.g., "File updated" or "Patch applied")
                elif kind == "on_chain_end":
                    # Filter for our specific logic nodes
                    if event["name"] in ["generate_patch", "generate_code", "search_code"]:
                        output = event["data"].get("output", {})
                        if "messages" in output and output["messages"]:
                            last_msg = output["messages"][-1].content
                            # Check if the node actually produced a response
                            yield json.dumps({
                                "message": {"role": "assistant", "content": f"\n\n🛠️ [Agent]: {last_msg}"},
                                "done": False
                            }) + "\n"

            yield json.dumps({"done": True})
            
        except Exception as e:
            yield json.dumps({
                "message": {"role": "assistant", "content": f"\n\n[Backend Error]: {str(e)}"},
                "done": True
            }) + "\n"

    return StreamingResponse(stream_copilot_response(), media_type="application/x-ndjson")


@app.get("/api/tags")
async def get_tags():
    return {"models": [{"name": "gpt-oss:120b-cloud", "model": "gpt-oss:120b-cloud"}]}


@app.post("/api/show")
async def show_model_info(request: Request):
    """
    Returns model details. Continue calls this to understand 
    the model's context length and stop sequences.
    """
    body = await request.json()
    model_name = body.get("name", "gpt-oss:120b-cloud")
    
    return {
        "modelfile": f"FROM {model_name}",
        "parameters": "stop           \"<|end_of_text|>\"\nstop           \"<|eot_id|>\"",
        "template": "{{ .System }}\nUSER: {{ .Prompt }}\nASSISTANT: ",
        "details": {
            "format": "gguf",
            "family": "llama",
            "parameter_size": "120B",
            "quantization_level": "Q4_K_M"
        }
    }



if __name__ == "__main__":
    import uvicorn # type: ignore
    uvicorn.run(app, host="0.0.0.0", port=8000)