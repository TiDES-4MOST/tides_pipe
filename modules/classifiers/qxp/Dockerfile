FROM python:3.11

WORKDIR /app

COPY requirements.txt .

RUN pip install -r requirements.txt

COPY . .

ENV SETUPTOOLS_SCM_PRETEND_VERSION=1.0.0

RUN pip install .

CMD ["uvicorn", "run_qxp:app", "--host", "0.0.0.0", "--port", "8000"]
