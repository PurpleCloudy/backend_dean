"""Local CPU BGE-M3 endpoint matching the backend and unchanged agent contract."""
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

MODEL_PATH = Path(__file__).resolve().parents[1] / '.local/bge/model'
inference_lock = Lock()


@asynccontextmanager
async def lifespan(app):
    import torch
    from FlagEmbedding import BGEM3FlagModel

    torch.set_num_threads(4)
    app.state.model = BGEM3FlagModel(
        str(MODEL_PATH), devices='cpu', use_fp16=False,
    )
    yield
    del app.state.model


app = FastAPI(title='Local BGE-M3', lifespan=lifespan)


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    text: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32768)]] = Field(min_length=1, max_length=8)
    return_dense: Literal[True] = True
    return_sparse: Literal[True] = True
    return_colbert: Literal[False] = False


@app.get('/health')
async def health():
    if not hasattr(app.state, 'model'):
        raise HTTPException(503, 'Model is not loaded')
    return {'status': 'ok', 'model': 'BAAI/bge-m3', 'device': 'cpu', 'dimension': 1024}


@app.post('/predict')
def predict(body: PredictRequest):
    if not hasattr(app.state, 'model'):
        raise HTTPException(503, 'Model is not loaded')
    # Avoid simultaneous CPU batches exhausting memory alongside the local LLM.
    if not inference_lock.acquire(blocking=False):
        raise HTTPException(503, 'Embedding service is busy', headers={'Retry-After': '1'})
    try:
        model = app.state.model
        tokens = model.tokenizer(body.text, truncation=False, padding=False)['input_ids']
        if any(len(row) > 8192 for row in tokens):
            raise HTTPException(422, 'A text exceeds the 8192-token model limit')
        output = model.encode(
            body.text, batch_size=1, max_length=8192,
            return_dense=True, return_sparse=True, return_colbert_vecs=False,
        )
        return {
            'vector': output['dense_vecs'].tolist(),
            'sparse': [{str(key): float(value) for key, value in row.items()}
                       for row in output['lexical_weights']],
        }
    finally:
        inference_lock.release()


if __name__ == '__main__':
    import uvicorn

    uvicorn.run(app, host='127.0.0.1', port=8231, access_log=False)
