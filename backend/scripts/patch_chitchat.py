import re

with open("app/rag/chat.py", "r", encoding="utf-8") as f:
    content = f.read()

helper_code = '''
    @staticmethod
    def _get_fast_chitchat_response(question: str, lang: str):
        """Instant sub-millisecond response for common greetings and pleasantries."""
        import re
        q = question.strip().lower()
        q_clean = re.sub(r"[^a-zA-Z0-9\s]", "", q).strip()

        greetings = {
            "hi", "hello", "hey", "hiya", "salam", "assalam o alaikum", "assalamu alaikum",
            "assalam alaikum", "salam alaikum", "aaoa", "good morning", "good afternoon",
            "good evening", "good day", "kaise ho", "kya hal hai", "how are you", "hello there",
            "hi there", "hy", "salam bhai", "salam sir"
        }
        if q_clean in greetings:
            if lang == "roman_urdu":
                return "Walaikum Assalam! Main aap ki pharmacy, sales aur inventory records mein kya madad kar sakta hoon?"
            elif lang == "urdu_script":
                return "السلام علیکم! میں آپ کی فارمیسی، انوینٹری یا سیلز ڈیٹا کے بارے میں کیا مدد کر سکتا ہوں؟"
            return "Hello! How can I assist you today? I'm here to help with your local business, sales, and inventory data."

        thanks = {"thanks", "thank you", "thx", "shukriya", "bohot shukriya", "dhanyawad", "many thanks", "thanks a lot"}
        if q_clean in thanks:
            if lang == "roman_urdu":
                return "Aap ka bohot shukriya! Agar aap ko kisi aur record ya report ke baray mein janna ho toh zaroor batayein."
            elif lang == "urdu_script":
                return "بہت شکریہ! اگر آپ کو کسی اور ریکارڈ یا رپورٹ کی ضرورت ہو تو ضرور بتائیں۔"
            return "You're very welcome! Let me know if you need anything else from your connected records."

        identity = {"who are you", "what are you", "tum kaun ho", "aap kaun hain", "kya kar sakte ho", "what can you do"}
        if q_clean in identity:
            if lang == "roman_urdu":
                return "Main LLM-Konnect ka local AI assistant hoon. Main aap ke POS sales, purchase, inventory aur product catalogs ka tajziya kar ke aap ke sawalat ke foran jawabat deta hoon."
            return "I am the LLM-Konnect local AI assistant. I help you query, analyze, and generate reports from your connected pharmacy POS, sales, and inventory datasets."

        return None
'''

if "_get_fast_chitchat_response" not in content:
    target = "    @staticmethod\n    def _direct_record_answer(question: str, chunks):"
    if target in content:
        content = content.replace(target, helper_code + "\n" + target, 1)
        print("Inserted _get_fast_chitchat_response.")
    else:
        print("Target for helper not found.")

fast_call_ask = '''        if route == RouteType.CHITCHAT:
            fast_reply = self._get_fast_chitchat_response(intent_question, lang)
            if fast_reply:
                timing = round(time.time() - start_time, 3)
                session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
                session_manager.append_turn(request.session_id, "assistant", fast_reply, domain=request.domain, route=RouteType.CHITCHAT, sources=None, timing=timing)
                return ChatResponse(answer=fast_reply, route=RouteType.CHITCHAT, sources=[], computed_values=None, session_id=request.session_id, timing=timing)'''

fast_call_stream = '''        if route == RouteType.CHITCHAT:
            fast_reply = self._get_fast_chitchat_response(intent_question, lang)
            if fast_reply:
                timing = round(time.time() - start_time, 3)
                session_manager.append_turn(request.session_id, "user", question, domain=request.domain)
                session_manager.append_turn(request.session_id, "assistant", fast_reply, domain=request.domain, route=RouteType.CHITCHAT, sources=None, timing=timing)
                yield json.dumps({"chunk": fast_reply, "route": RouteType.CHITCHAT, "sources": [], "computed_values": None}) + "\\n"
                return'''

target_chitchat = "        if route == RouteType.CHITCHAT:\n            pass"
if target_chitchat in content:
    content = content.replace(target_chitchat, fast_call_ask, 1)
    content = content.replace(target_chitchat, fast_call_stream, 1)
    print("Replaced CHITCHAT branches.")

with open("app/rag/chat.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Saved updated app/rag/chat.py.")
