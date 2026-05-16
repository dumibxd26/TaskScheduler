# ts-scheduler Helm chart

Skeleton chart. The static manifests in `k8s/` and `crds/` are the
source of truth today; this chart will progressively template them.

For now use:

```sh
kubectl apply -k manifests/
```

When the chart templates are filled in, `helm install ts-scheduler ./charts/ts-scheduler`
will produce the same result.
