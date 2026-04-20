# `prod-pipeline` — the production branch for both Azure pipelines

This branch is consumed by two production pipelines on the user's fork
(`rodchristiansen/autopkg`):

- **Munki pipeline** — `/Users/rod/DevOps/Munki/pipelines/munki-autopkg-run.yml`
  (line 31: `AUTOPKG_BRANCH: prod-pipeline`)
- **Cimian pipeline** — `/Users/rod/DevOps/Cimian/pipelines/cimian-autopkg-run.yml`
  (line 32: `AUTOPKG_BRANCH: prod-pipeline`)

It stacks every change both pipelines need on top of `upstream/dev-2.x`,
organised so that individual commits can be **dropped as upstream absorbs
the same work**. The long-term goal is for this branch to shrink to only
genuinely production-local code (MunkiVersionChecker, name+version dedup,
Cimian processors).

## Revert

One-line change per pipeline YAML:

- Munki: `AUTOPKG_BRANCH: prod-pipeline` → `AUTOPKG_BRANCH: main`
- Cimian: `AUTOPKG_BRANCH: prod-pipeline` → `AUTOPKG_BRANCH: add-cimian-support`

Both `main` and `add-cimian-support` are preserved untouched on the fork.

## Current commit stack (bottom → top)

Each row: commit on this branch, its origin, and the condition under which
it can be dropped from this branch (because upstream will ship it).

| # | Commit | Origin | Drop when |
|---|---|---|---|
| 1 | Add autopkgyaml helpers for Munki pkginfo serialization | `pr/yaml-core` | autopkg/autopkg PR for core yaml merges upstream |
| 2 | Route Munki processors through autopkgyaml parse/save helpers | `pr/yaml-processors` | autopkg/autopkg PR for processors merges upstream |
| 3 | Teach munkirepolibs to write/read yaml pkginfo and catalogs | `pr/yaml-repo-libs` | autopkg/autopkg PR for repo-libs merges upstream |
| 4 | Accept YAML recipe lists in parse_recipe_list | `pr/yaml-recipe-list` | autopkg/autopkg PR for recipe-list merges upstream |
| 5 | Remove distutils dependency for Python 3.12 compat | `main` (`44e22ba`) | `upstream/new-aplooseversion` merges into `dev-2.x` |
| 6 | Add MunkiVersionChecker processor for version pre-checking | `main` (`c9069cf`) | — production-local |
| 7 | Auto-inject MunkiVersionChecker in recipe processing engine | `main` (`e3b3faa`) | — production-local |
| 8 | Add name+version fallback to MunkiImporter duplicate detection | `main` (`995f5e4`) | — production-local |
| 9 | Move name+version match before installs check, fix arch gate | `main` (`7653b24`) | — production-local |
| 10 | Add Cimian processors: CimianImporter, CimianInfoCreator, CimianCatalogBuilder | `main` (`1fb4e86`) | — production-local (separate backend) |
| 11 | CimianInfoCreator: add MSIX/APPX metadata extraction | `add-cimian-support` (`1e047c2`) | — production-local |
| 12 | CimianImporter: architecture-aware duplicate detection | `add-cimian-support` (`b6706a5`) | — production-local |
| 13 | CimianImporter: include supported_architectures in report data | `add-cimian-support` (`d0c9214`) | — production-local |
| 14 | Fix MSI property extraction and CURL_PATH default for Windows | `add-cimian-support` (`7d1d167`) | — production-local |
| 15 | CimianInfoCreator: fix MSI SQL query — use OR instead of IN | `add-cimian-support` (`b6bbf8e`) | — production-local |
| 16 | CimianImporter: stringify supported_architectures in report data | `add-cimian-support` (`e5dfd34`) | — production-local |
| 17 | CimianImporter: version in pkg filename and installer location | `add-cimian-support` (`f6cba11`) | — production-local |
| 18 | Add icon_name support to CimianInfoCreator and CimianImporter | `add-cimian-support` (`83267f9`) | — production-local |

Note on `imp → importlib`: upstream handled this independently in
`5e2eb52` on `dev-2.x`, so the user's equivalent commit `7265f65` from
`main` is intentionally not carried here — it rides in via the base.

## Rebase protocol (what to do when upstream lands any of the above)

1. Fetch: `git fetch upstream dev-2.x`
2. Start the rebase: `git rebase --onto upstream/dev-2.x upstream/dev-2.x prod-pipeline`
   (or interactive: `git rebase -i upstream/dev-2.x`).
3. For each commit upstream has absorbed, `drop` it in the interactive
   editor (or remove it from the cherry-pick plan).
4. Expect trivial conflicts where you've edited the same import lines
   upstream now also edits. Resolve by keeping the upstream form (since
   the local commit is about to be dropped anyway).
5. Run the verification commands below. If green, force-push:
   `git push --force-with-lease origin prod-pipeline`.
6. Update this table: move the dropped row out of the stack, keep the
   remaining rows renumbered.

The 4 YAML PR branches (`pr/yaml-core`, `pr/yaml-processors`,
`pr/yaml-repo-libs`, `pr/yaml-recipe-list`) are the **upstream-facing**
source of truth for commits #1–#4. When any of those PRs merge upstream,
drop the matching commit here.

## Verification commands

Run from repo root on a fresh checkout of `prod-pipeline`:

```sh
# Static parse
python3 -c "import ast; ast.parse(open('Code/autopkg').read())"

# No ghost YAML helpers (only a comment reference is acceptable)
grep -rn "_literal_representer\|_LiteralStr\|MunkiPkginfoDumper(yaml\.SafeDumper)" \
     Code/autopkglib/ | grep -v "^.*:.*#"

# No remaining imp or distutils imports in runtime code
grep -rn "^import imp\b\|^from imp\b\|^from distutils\|^import distutils" \
     Code/autopkglib/ Code/autopkg

# Auto-inject hook still wired
grep -n "MunkiVersionChecker" Code/autopkglib/__init__.py | head -3

# Name+version dedup present
grep -n "name_version\|name_versions" \
     Code/autopkglib/MunkiImporter.py Code/autopkglib/munkirepolibs/AutoPkgLib.py
```

Then a YAML round-trip + recipe-list parse:

```sh
python3 <<'PY'
import sys, importlib.util, types
spec = importlib.util.spec_from_file_location(
    "autopkglib.autopkgyaml", "Code/autopkglib/autopkgyaml/__init__.py"
)
m = importlib.util.module_from_spec(spec)
pkg = types.ModuleType("autopkglib"); pkg.__path__ = ["Code/autopkglib"]
sys.modules["autopkglib"] = pkg
sys.modules["autopkglib.autopkgyaml"] = m
spec.loader.exec_module(m)
pkginfo = {"name": "X", "version": "10.10", "minimum_os_version": "11.0"}
parsed = m.loads_pkginfo_yaml(m.dumps_pkginfo_yaml(pkginfo))
assert parsed["version"] == "10.10" and parsed["minimum_os_version"] == "11.0"
import yaml
d = yaml.load("recipes: [a]\nInput:\n  version: 10.10\n",
              Loader=m.AutoPkgYAMLLoader)
assert d["Input"]["version"] == "10.10"
print("prod-pipeline: YAML round-trip + recipe-list OK")
PY
```

## Out-of-scope

- Cimian processors still call `yaml.safe_load` / `yaml.dump` directly
  rather than through `autopkglib.autopkgyaml`. Optional consolidation
  follow-up — left alone because the Munki-oriented dumper enforces
  Munki-specific key orderings that would reorder Cimian pkginfo fields.
- Nothing here goes upstream; the 4 PR branches are the upstream path.
