from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

# Allow frontend to access the backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust this in production
    allow_methods=["*"],
    allow_headers=["*"],
)

class Message(BaseModel):
  text: str

@app.post("/hello")
def say_hello(data: Message):
  return {
    "reply": f"Hello, you sent:{data.text}"
  }