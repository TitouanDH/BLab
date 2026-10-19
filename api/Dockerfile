# Use a base image with Python installed
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set the working directory in the container
WORKDIR /app

# Install dependencies with trusted hosts to bypass SSL issues
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org -r requirements.txt

# Copy the Django project files into the container
COPY . /app/

# Collect static files
RUN python manage.py collectstatic --noinput

# Run Gunicorn: workers, threads and timeout in gunicorn.conf.py
CMD ["gunicorn", "wsgi:application", "-c", "gunicorn.conf.py"]
