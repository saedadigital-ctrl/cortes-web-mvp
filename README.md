# Cortes Web MVP

Projeto inicial com backend em FastAPI e frontend em Next.js.

## Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Endpoints disponíveis:

- `GET /status`: verifica a saúde da aplicação.
- `POST /jobs`: recebe um payload `{ "link": "<url>" }`, retorna um identificador com status `pending` e inicia o processamento em background (download do vídeo, transcrição com Whisper, geração dos cortes automáticos e salvamento em `/data`).
- `GET /jobs/{job_id}`: consulta o status do job (`pending`, `processing`, `cutting`, `done` ou `error`) e traz os caminhos para o vídeo completo, para a transcrição e para cada corte gerado (arquivos `.mp4` e `.srt`).

## Frontend

```bash
cd frontend
npm install
npm run dev
```

A página inicial possui um campo de texto e um botão "Testar API" que consome o endpoint `/status` do backend e mostra o resultado.
