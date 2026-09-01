from fastapi import Depends, FastAPI

app = FastAPI()


def get_token_header(x_token: str = "demo"):
    return {"token": x_token}


@app.get("/items/")
def read_items(token: dict = Depends(get_token_header)):
    return token