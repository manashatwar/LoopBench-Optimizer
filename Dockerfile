# Use an official Python image as the base
FROM python:3.12-slim

# Set the working directory inside the container
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy the project files into the container
COPY . /app

# Install Python dependencies
RUN pip install --root-user-action=ignore -e .

# Expose the project directory as a volume
VOLUME ["/app"]

# Default to the LoopBench CLI. Pass subcommands/args at `docker run`, e.g.:
#   docker run --rm -v $PWD:/app loopbench run --target . --metric latency
ENTRYPOINT ["loopbench"]
CMD ["--help"]
