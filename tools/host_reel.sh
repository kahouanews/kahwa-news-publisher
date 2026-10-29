#!/usr/bin/env bash
# Host a reel MP4 on GitHub Pages (branch reels-media -> https://<owner>.github.io/<repo>/<file>.mp4).
# Why Pages: raw.githubusercontent.com serves MP4 as application/octet-stream and GitHub Release assets answer with a
# redirect + octet-stream; Buffer needs a direct, public, stable HTTPS file URL and Zernio infers the type from the
# URL/HEAD. Pages serves video/mp4 directly (no redirect). Zernio additionally re-hosts the file on its own storage at
# publish time (providers.Zernio.stage_video). Keeps 5 days of files (orphan branch, force-pushed: no history bloat).
#
# usage: bash tools/host_reel.sh <file.mp4>      env: GH_TOKEN, GITHUB_REPOSITORY      prints the served URL on success
set -euo pipefail
F="$1"; NAME=$(basename "$F")
OWNER="${GITHUB_REPOSITORY%%/*}"; REPO="${GITHUB_REPOSITORY##*/}"
URL="https://${OWNER}.github.io/${REPO}/${NAME}"
W=$(mktemp -d); cp "$F" "$W/$NAME"; mkdir "$W/site"; cd "$W/site"
 git init -q; git remote add origin "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
if git fetch -q --depth=1 origin reels-media; then git checkout -q FETCH_HEAD; fi
cut=$(date -u -d '5 days ago' +%Y%m%d)
for f in *.mp4; do [ -e "$f" ] || continue; p="${f#test-}"; [[ "${p:0:8}" =~ ^[0-9]{8}$ ]] && [ "${p:0:8}" -lt "$cut" ] && rm -f "$f"; done
touch .nojekyll
[ -e robots.txt ] || printf 'User-agent: *\nDisallow: /\n' > robots.txt
mv "../$NAME" .
git checkout -q --orphan new
git add -A
git -c user.name=kahwa-bot -c user.email=kahwa-bot@users.noreply.github.com commit -qm "reels media $(date -u +%FT%TZ)"
git push -qf origin new:reels-media
# wait for the Pages build, then insist on a direct 200 with Content-Type video/mp4 and the right size
SIZE=$(stat -c %s "$NAME")
for i in $(seq 1 40); do
  H=$(curl -sI "$URL" || true)
  code=$(echo "$H" | head -1 | awk '{print $2}')
  ctype=$(echo "$H" | grep -i '^content-type:' | tr -d '\r' | awk '{print $2}')
  clen=$(echo "$H" | grep -i '^content-length:' | tr -d '\r' | awk '{print $2}')
  if [ "$code" = "200" ] && [ "$ctype" = "video/mp4" ] && { [ -z "$clen" ] || [ "$clen" = "$SIZE" ]; }; then
    echo "served: $URL (video/mp4, $SIZE bytes, after $((i*15))s)" >&2; echo "$URL"; exit 0
  fi
  sleep 15
done
echo "MP4 not served correctly at $URL (last: HTTP ${code:-?}, type ${ctype:-?}, length ${clen:-?})" >&2
exit 1
