from .common import PY, RI, VM, Dash, dashboard, right_axis, ts, vm
from .deus import ANNOTATIONS, APP, LINKS_DEUS

FILE = "deus-profiling.json"

CPU = "process_cpu:cpu:nanoseconds:cpu:nanoseconds"
RUNTIME = f'process_runtime_heap_used_bytes{{{APP}, service_name="$service"}}'
REQ = "http_server_request_duration_seconds"
S = f'{APP}, service_name="$service", http_route!~"/openapi.*"'
SELECTED = f'{APP}, service_name="$service", service_version="$version", k8s_pod_name=~"${{pod:regex}}"'
DRILLDOWN = ("/a/grafana-pyroscope-app/explore?var-dataSource=Pyroscope&var-serviceName=$service"
             f"&var-profileMetricId={CPU}&explorationType=")


def profile(selector, ref="A"):
    return {"datasource": PY, "refId": ref, "queryType": "profile", "profileTypeId": CPU,
            "labelSelector": f"{{{selector}}}", "groupBy": []}


def cpu_series(selector, group_by, ref="A"):
    return {"datasource": PY, "refId": ref, "queryType": "metrics", "profileTypeId": CPU,
            "labelSelector": f"{{{selector}}}", "groupBy": group_by}


def flamegraph(title, selector, desc):
    return {"type": "flamegraph", "title": title, "description": desc, "datasource": PY, "options": {},
            "fieldConfig": {"defaults": {}, "overrides": []}, "targets": [profile(selector)]}


def legend(p, label):
    for t in p["targets"]:
        t["legendFormat"] = label
    return p


d = Dash()
d.row("All services — profiled CPU")
legend(d.add(ts("Profiled CPU by service", [cpu_series(APP, ["service_name"])], "cores", ds=PY, stack=True,
                min_=0, desc="JavaScript CPU each service spends, from the profiles of all its pods. The largest band "
                             "is where an optimisation pays most; a band that jumps after a rollout (blue "
                             "annotation) is a regression to open below."), 24, 8), "{{service_name}}")

d.row("$service — when did it get slow?")
d.add(ts("p95 latency and requests", [
    vm(f"histogram_quantile(0.95, sum by (le) (rate({REQ}_bucket{{{S}}}{RI})))", "p95 latency"),
    vm(f"sum(rate({REQ}_count{{{S}}}{RI}))", "requests / s", "B")],
    "s", min_=0, overrides=[right_axis("B", "reqps")],
    desc="Server-side p95 response time (left) and request rate (right). Zoom into a spike: every panel below, "
         "flame graphs included, follows the selected time range."), 8, 8)
d.add(ts("5xx per second", [
    vm(f'sum(rate({REQ}_count{{{S}, http_response_status_code=~"5.."}}{RI})) or vector(0)', "5xx / s")],
    "reqps", min_=0, desc="Server errors of $service. CPU-bound code shows as latency first; errors that line up "
                          "with a CPU spike point at timeouts or exhausted pools under load."), 8, 8)
legend(d.add(ts("Profiled CPU by pod", [cpu_series(f'{APP}, service_name="$service", service_version="$version"',
                                                  ["k8s_pod_name"])],
                "cores", ds=PY, min_=0,
                desc="JavaScript CPU per pod of $service at version $version. One pod far above the others means "
                     "uneven load or a hot tenant; pick it in the Pod filter to see its flame graph alone."), 8, 8),
       "{{k8s_pod_name}}")

d.row("$service — where does the CPU go?")
d.add(flamegraph("CPU flame graph — $service $version", SELECTED,
                 "Every stack the profiler sampled in the selected time range, merged. Width is CPU time: the widest "
                 "frames at the top are the hot functions, the frames below them are their callers. The table ranks "
                 "functions by self time (spent in the function itself) and total time (including what it calls); "
                 "click a row or a frame to focus on it. Anonymous functions are named by package or file and line. "
                 "Only JavaScript on the main thread is sampled: waiting for the database or another service does "
                 "not appear here (use the slow traces), native work such as TLS or compression does not either."),
      24, 16)

d.row("$service — $version against $compare")
for version in ("$version", "$compare"):
    d.add(flamegraph(f"CPU flame graph — {version}", f'{APP}, service_name="$service", service_version="{version}"',
                     "Profiles of one version of $service in the selected time range. Put the release after a "
                     "regression in Version and the one before it in Compare with: a frame that is wider on the "
                     "left is code the new release spends more CPU in. The Profiles Drilldown link at the top "
                     "draws the difference as one diff flame graph."), 12, 14)
legend(d.add(ts("Profiled CPU by version", [cpu_series(f'{APP}, service_name="$service"', ["service_version"])],
                "cores", ds=PY, min_=0,
                desc="CPU of $service per running version. During a rollout both appear; after it, compare the "
                     "level of the new version with the old one at a similar request rate."), 24, 7),
       "{{service_version}}")

d.row("All services — shared code")
d.add(flamegraph("CPU flame graph — all deus services", APP,
                 "The profiles of every deus service merged. Libraries every service uses (OpenTelemetry export, "
                 "Drizzle, Elysia, JSON) stand out here even when each service spends little on them, which is "
                 "where a fix in a shared package helps everywhere."), 24, 14)



def version_var(name, label, order, hide=0):
    query = (f"query_result({order}(min by (service_version) "
             f"(min_over_time(timestamp({RUNTIME})[$__range]))))")
    return {"name": name, "label": label, "type": "query", "datasource": VM, "hide": hide,
            "query": {"query": query, "refId": name}, "definition": query, "regex": '/service_version="([^"]+)"/',
            "refresh": 2, "sort": 0, "multi": False, "includeAll": False}


POD_QUERY = (f'label_values(process_runtime_heap_used_bytes{{{APP}, service_name="$service", '
             f'service_version="$version"}}, k8s_pod_name)')
VARIABLES = [
    {"name": "service", "label": "Service", "type": "query", "datasource": PY,
     "query": {"type": "labelValue", "profileTypeId": CPU, "labelName": "service_name", "refId": "service"},
     "refresh": 2, "sort": 1, "multi": False, "includeAll": False,
     "current": {"selected": True, "text": "ledger", "value": "ledger"}},
    version_var("version", "Version", "sort_desc"),
    version_var("compare", "Compare with", "sort"),
    {"name": "pod", "label": "Pod", "type": "query", "datasource": VM,
     "query": {"query": POD_QUERY, "refId": "pod"}, "definition": POD_QUERY,
     "refresh": 2, "sort": 1, "multi": True, "includeAll": True, "allValue": ".+",
     "current": {"selected": True, "text": "All", "value": "$__all"}},
]

LINKS = LINKS_DEUS + [
    {"title": "Deus — Service", "type": "link", "url": "/d/deus-service/deus-service?var-service=$service",
     "keepTime": True},
    {"title": "Deus — Traces", "type": "link", "url": "/d/deus-traces/deus-traces?var-service=$service",
     "keepTime": True},
    {"title": "Deus — Logs", "type": "link", "url": "/d/deus-logs/deus-logs?var-service=$service", "keepTime": True},
    {"title": "Profiles Drilldown: diff of two time ranges", "type": "link", "url": DRILLDOWN + "diff-flame-graph",
     "keepTime": True, "targetBlank": True},
]

DASHBOARD = dashboard(
    "deus-profiling", "Deus — Profiling", ["deus", "profiling"], d.panels, VARIABLES,
    "Where the deus services spend CPU, from the continuous profiles in Pyroscope: CPU per service, the symptoms of one "
    "service (latency, errors, CPU per pod) to pick the time range, its flame graph, two versions side by side, and "
    "the code all services share.",
    "now-1h", links=LINKS, annotations=ANNOTATIONS)
