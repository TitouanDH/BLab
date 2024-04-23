# Use a base image with Python installed
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set the working directory in the container
WORKDIR /app

# Install dependencies
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the Django project files into the container
COPY . /app/

# Collect static files
RUN python manage.py collectstatic --noinput

# Expose the port that Gunicorn will listen on
EXPOSE 8000

# Run Gunicorn
CMD ["gunicorn", "myproject.wsgi:application", "-b", "0.0.0.0:8000"]