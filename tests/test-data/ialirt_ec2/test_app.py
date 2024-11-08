from flask import Flask
import multiprocessing

# Function to create a Flask application for a specific port
def create_app(port):
    app = Flask(__name__)

    @app.route("/")
    def hello():
        return f"Hello from Port {port}!", 200

    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    # Define the ports on which to run the Flask instances
    ports = [1234, 1235]
    processes = [multiprocessing.Process(target=create_app, args=(port,)) for port in ports]

    for process in processes:
        process.start()

    for process in processes:
        process.join()
