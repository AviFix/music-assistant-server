# Use a base image with Python
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory
WORKDIR /app

# Copy the requirements file
COPY requirements_all.txt .

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies with timeout
RUN pip install --upgrade pip \
    && pip install --upgrade uv \
    && pip install requests_toolbelt \
    && pip install -r requirements_all.txt --timeout 100

# Copy the application code
COPY . .

# Expose the ports the app runs on
EXPOSE 8095 8097

# Command to run the application
CMD ["python3", "-m", "music_assistant"]
