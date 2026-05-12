import json
import plotly.express as px
import os

OUTPUT_DIR = "charts_html"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def extract_chart_json(report_text):

    try:
        start = report_text.find("[")
        end = report_text.rfind("]") + 1

        json_str = report_text[start:end]

        return json.loads(json_str)

    except:
        return []


def generate_charts(report_text):

    charts = extract_chart_json(report_text)

    paths = []

    for i, c in enumerate(charts):

        try:
            if c["chart_type"] == "bar":
                fig = px.bar(x=c["x"], y=c["y"], title=c["title"])

            elif c["chart_type"] == "line":
                fig = px.line(x=c["x"], y=c["y"], title=c["title"])

            else:
                continue

            path = f"{OUTPUT_DIR}/chart_{i}.html"
            fig.write_html(path)

            paths.append(path)

        except:
            continue

    return paths