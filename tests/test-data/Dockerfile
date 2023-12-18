FROM python:3.10
COPY test_app.py /app/app.py
WORKDIR /app
RUN pip install flask
EXPOSE 8080
CMD ["python", "app.py"]