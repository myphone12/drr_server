from flask import Flask
import datetime
app = Flask(__name__)

@app.route("/DanceRail/<name>")
def hello(name):
    if name == "usecard.php":
        return "10"
    if name == "date.php":
        ct = datetime.datetime.now()
        return f"{ct.year}{ct.month}{ct.day}"

if __name__ == "__main__":
    app.run()