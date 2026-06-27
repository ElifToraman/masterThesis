# Start from an official Python image (slim variant = smaller)
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Copy requirements first (Docker caches this layer if it doesn't change)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the actual application code
COPY app.py .

# Document which port the container listens on (informational)
EXPOSE 8080

# Default port; Knative will set PORT but having a default is good practice
ENV PORT=8080

# Command to run when the container starts
CMD ["python", "app.py"]
