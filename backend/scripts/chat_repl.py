#!/usr/bin/env python
import os
import sys

# Ensure backend directory is in PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.rag.models import ChatRequest
from app.rag.chat import rag_chat
from app.rag.history import session_manager
from app.schema.domain import registry
import app.schema  # trigger domain registration

def main():
    print("Welcome to LLM-Konnect Chat REPL")
    print("Type 'exit' or 'quit' to exit.")
    
    session_id = session_manager.start_session()
    print(f"Started session: {session_id}\n")
    
    while True:
        try:
            q = input("\nYou: ")
        except (KeyboardInterrupt, EOFError):
            break
            
        if q.lower() in ("exit", "quit"):
            break
            
        if not q.strip():
            continue
            
        req = ChatRequest(question=q, session_id=session_id)
        
        print("\nThinking...")
        try:
            resp = rag_chat.ask(req)
            print(f"\nAssistant [{resp.route}]: {resp.answer}")
            
            if resp.computed_values:
                print(f"\n[Computed]: {resp.computed_values}")
                
            if resp.sources:
                print("\n[Sources]:")
                for s in resp.sources:
                    print(f"  - {s.label} (Row {s.source_row})")
                    
            print(f"\n[Time: {resp.timing:.2f}s]")
            
        except Exception as e:
            print(f"\nError: {e}")

if __name__ == "__main__":
    main()
