# Hello Knative Python Function

This is a simple Python Flask function used to test Knative Serving on the edge-cloud Chameleon Cloud testbed.

## Build and push

    docker buildx build --platform linux/amd64 -t eltoraman/hello-knative:v2 --push .

## Expected output

    Hello Edge Cluster! This is my Knative serverless function.
