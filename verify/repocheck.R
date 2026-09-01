# An independent implementation of check_repo, in base R.
#
# repo_contains is the tool this repo uses on itself: it is how a claim about
# what a file says gets checked. Its behaviour rests on two decisions that are
# easy to get wrong and invisible when they are wrong. A non regex pattern must
# be escaped before it is searched, otherwise "asdict(self)" quietly becomes a
# group and matches "asdictself" instead. And the hit cap must stop counting at
# max_hits rather than after it, or a capped result reports a number that is not
# the number of hits.
#
# This walks the same files and reapplies the same two rules, written from the
# description above rather than from the Python. It must agree with
# verify/cases_repo.tsv on the verdict and the hit count for every row.
#
#     Rscript verify/repocheck.R <repo-root>

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1L) {
  cat("usage: repocheck.R <repo-root>\n")
  quit(status = 2L)
}
root <- normalizePath(args[1], mustWork = TRUE)
max_hits <- 5L

unescape <- function(s) {
  out <- character(0)
  chars <- strsplit(s, "", fixed = TRUE)[[1]]
  i <- 1L
  while (i <= length(chars)) {
    if (chars[i] == "\\" && i < length(chars)) {
      nxt <- chars[i + 1L]
      out <- c(out, switch(nxt, n = "\n", t = "\t", "\\" = "\\", paste0("\\", nxt)))
      i <- i + 2L
    } else {
      out <- c(out, chars[i])
      i <- i + 1L
    }
  }
  paste(out, collapse = "")
}

# Every file under `path`, in the same set Python's rglob("*") walks: hidden
# files included, anything under .git excluded, directories are not files.
files_under <- function(path) {
  if (!dir.exists(path)) return(path)
  found <- list.files(path, recursive = TRUE, all.files = TRUE,
                      full.names = TRUE, no.. = TRUE, include.dirs = FALSE)
  found[!grepl(".git/", found, fixed = TRUE)]
}

count_hits <- function(pattern, path, is_regex) {
  if (!file.exists(path)) return(list(status = "unverifiable", hits = 0L))
  # A pattern that will not compile is unverifiable, not "no hits found": the
  # difference matters, because "no hits" would read as a refutation.
  ok <- tryCatch({
    grepl(pattern, "probe", perl = is_regex, fixed = !is_regex)
    TRUE
  }, error = function(e) FALSE, warning = function(w) FALSE)
  if (!ok) return(list(status = "unverifiable", hits = 0L))

  hits <- 0L
  for (f in files_under(path)) {
    lines <- tryCatch(readLines(f, warn = FALSE), error = function(e) character(0))
    if (length(lines) == 0L) next
    matched <- tryCatch(
      sum(grepl(pattern, lines, perl = is_regex, fixed = !is_regex)),
      error = function(e) 0L)
    hits <- hits + as.integer(matched)
    if (hits >= max_hits) {
      hits <- max_hits
      break
    }
  }
  list(status = if (hits > 0L) "checked" else "refuted", hits = hits)
}

table_path <- file.path(root, "verify", "cases_repo.tsv")
raw <- readLines(table_path, warn = FALSE)
raw <- raw[nzchar(raw)]
if (length(raw) < 2L) {
  cat(sprintf("no cases read from %s\n", table_path))
  quit(status = 1L)
}

rows <- 0L
bad <- 0L
for (line in raw[-1]) {
  field <- strsplit(line, "\t", fixed = TRUE)[[1]]
  rows <- rows + 1L
  if (length(field) != 6L) {
    cat(sprintf("  row %d: expected 6 fields, found %d\n", rows, length(field)))
    bad <- bad + 1L
    next
  }
  id <- field[1]
  pattern <- unescape(field[2])
  path <- file.path(root, field[3])
  is_regex <- field[4] == "1"
  want_status <- field[5]
  want_hits <- suppressWarnings(as.integer(field[6]))

  got <- count_hits(pattern, path, is_regex)
  if (got$status != want_status || got$hits != want_hits) {
    cat(sprintf("  %s (%s in %s): python says %s/%d, R says %s/%d\n",
                id, field[2], field[3], want_status, want_hits,
                got$status, got$hits))
    bad <- bad + 1L
  }
}

if (bad > 0L) {
  cat(sprintf("R disagrees with Python on %d of %d check_repo cases\n", bad, rows))
  quit(status = 1L)
}
cat(sprintf("R reproduces all %d check_repo verdicts and hit counts\n", rows))
