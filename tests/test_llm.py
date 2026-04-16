import os
import dotenv
dotenv.load_dotenv()

os.environ['GOOGLE_API_KEY'] = os.getenv('GOOGLE_API_KEY')
os.environ['GEMINI_MODEL'] = os.getenv('GEMINI_MODEL')
import sys
sys.path.insert(0, 'e:/Personal Projects/cricket_hot_match_detector')
from bot.llm import get_llm
llm = get_llm()
result = llm.invoke('Say hello in one word.')
content = result.content
# content may be a plain string or a list of content blocks
if isinstance(content, list):
    text = " ".join(block["text"] for block in content if block.get("type") == "text")
else:
    text = content
print(text)