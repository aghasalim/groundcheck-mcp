/* An independent evaluator for check_math, in C.
 *
 * check_math is the one tool in this repo that computes rather than looks
 * something up, so it is the one that can be silently wrong. The Python side
 * hands the expression to ast.parse and walks the tree; the tests assert the
 * answers I expected. Both of those are the same implementation and the same
 * assumptions about precedence, so neither can catch the other.
 *
 * This is a recursive descent parser written from the language grammar rather
 * than from the Python code: it must agree with verify/cases_math.tsv on the
 * verdict for every row, and on the computed value where there is one. It has
 * to reject exactly what Python rejects too, a name or a call is not
 * arithmetic, and a parser that accepts one of those would be a hole.
 *
 *   cc -std=c99 -O2 -Wall -Wextra -Wpedantic -Werror -o matheval matheval.c -lm
 *   ./matheval <repo-root>
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define TOL 1e-6          /* the same relative tolerance verify.py uses */
#define VALUE_TOL 1e-12   /* agreement required on the computed value */

/* ---- parser ---------------------------------------------------------- */
/* expr  := term (('+'|'-') term)*
 * term  := factor (('*'|'/'|'//'|'%') factor)*
 * factor:= ('+'|'-') factor | power
 * power := atom ['**' factor]        right associative, unary allowed after **
 * atom  := number | '(' expr ')'
 */
typedef struct {
    const char *p;
    int failed;           /* set on a syntax error or a division by zero */
} Parser;

static double parse_expr(Parser *ps);

static void skip_ws(Parser *ps) {
    while (*ps->p == ' ' || *ps->p == '\t') ps->p++;
}

static double parse_atom(Parser *ps) {
    skip_ws(ps);
    if (*ps->p == '(') {
        ps->p++;
        double v = parse_expr(ps);
        skip_ws(ps);
        if (*ps->p != ')') { ps->failed = 1; return 0.0; }
        ps->p++;
        return v;
    }
    char *end = NULL;
    double v = strtod(ps->p, &end);
    if (end == ps->p) { ps->failed = 1; return 0.0; }
    /* strtod would happily read "nan" or "inf"; a literal in the Python AST
     * cannot be either, so those are a parse failure here as well. */
    if (!isfinite(v)) { ps->failed = 1; return 0.0; }
    ps->p = end;
    return v;
}

static double parse_factor(Parser *ps);

static double parse_power(Parser *ps) {
    double base = parse_atom(ps);
    if (ps->failed) return 0.0;
    skip_ws(ps);
    if (ps->p[0] == '*' && ps->p[1] == '*') {
        ps->p += 2;
        double e = parse_factor(ps);       /* right associative */
        if (ps->failed) return 0.0;
        return pow(base, e);
    }
    return base;
}

static double parse_factor(Parser *ps) {
    skip_ws(ps);
    if (*ps->p == '-') { ps->p++; return -parse_factor(ps); }
    if (*ps->p == '+') { ps->p++; return  parse_factor(ps); }
    return parse_power(ps);
}

static double parse_term(Parser *ps) {
    double v = parse_factor(ps);
    for (;;) {
        if (ps->failed) return 0.0;
        skip_ws(ps);
        if (ps->p[0] == '*' && ps->p[1] == '*') return v;   /* handled in power */
        if (ps->p[0] == '/' && ps->p[1] == '/') {
            ps->p += 2;
            double r = parse_factor(ps);
            if (ps->failed) return 0.0;
            if (r == 0.0) { ps->failed = 1; return 0.0; }
            v = floor(v / r);
        } else if (*ps->p == '*') {
            ps->p++;
            v *= parse_factor(ps);
        } else if (*ps->p == '/') {
            ps->p++;
            double r = parse_factor(ps);
            if (ps->failed) return 0.0;
            if (r == 0.0) { ps->failed = 1; return 0.0; }
            v /= r;
        } else if (*ps->p == '%') {
            ps->p++;
            double r = parse_factor(ps);
            if (ps->failed) return 0.0;
            if (r == 0.0) { ps->failed = 1; return 0.0; }
            /* Python's % takes the sign of the divisor, C's fmod does not. */
            v = fmod(v, r);
            if (v != 0.0 && ((v < 0.0) != (r < 0.0))) v += r;
        } else {
            return v;
        }
    }
}

static double parse_expr(Parser *ps) {
    double v = parse_term(ps);
    for (;;) {
        if (ps->failed) return 0.0;
        skip_ws(ps);
        if (*ps->p == '+') { ps->p++; v += parse_term(ps); }
        else if (*ps->p == '-') { ps->p++; v -= parse_term(ps); }
        else return v;
    }
}

/* Returns 1 and writes *out on success, 0 if the expression is not arithmetic
 * this evaluator accepts (which is the "unverifiable" verdict). */
static int evaluate(const char *expr, double *out) {
    Parser ps = { expr, 0 };
    double v = parse_expr(&ps);
    skip_ws(&ps);
    if (ps.failed || *ps.p != '\0') return 0;
    /* An expression that overflows has no value to compare a claim against.
     * Python raises there, so this has to refuse there too, and comparing
     * against an infinity is how the tool used to confirm any claim at all
     * about 1e300*1e300. */
    if (!isfinite(v)) return 0;
    *out = v;
    return 1;
}

/* ---- the verdict rule, reimplemented from the contract --------------- */
static const char *verdict(const char *expr, double claimed, double *value) {
    double actual;
    if (!evaluate(expr, &actual)) return "unverifiable";
    *value = actual;
    double limit = TOL * (fabs(actual) > 1.0 ? fabs(actual) : 1.0);
    return fabs(actual - claimed) <= limit ? "checked" : "refuted";
}

/* ---- tsv ------------------------------------------------------------- */
static void unescape(char *s) {
    char *w = s;
    for (const char *r = s; *r; r++) {
        if (*r != '\\') { *w++ = *r; continue; }
        r++;
        switch (*r) {
            case 'n': *w++ = '\n'; break;
            case 't': *w++ = '\t'; break;
            case '\\': *w++ = '\\'; break;
            default: *w++ = '\\'; *w++ = *r; break;
        }
    }
    *w = '\0';
}

static int split(char *line, char *field[], int max) {
    int n = 0;
    field[n++] = line;
    for (char *c = line; *c; c++) {
        if (*c == '\t') {
            *c = '\0';
            if (n >= max) return -1;
            field[n++] = c + 1;
        }
    }
    return n;
}

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "usage: matheval <repo-root>\n"); return 2; }

    char path[4096];
    snprintf(path, sizeof path, "%s/verify/cases_math.tsv", argv[1]);
    FILE *f = fopen(path, "r");
    if (!f) { fprintf(stderr, "cannot open %s\n", path); return 2; }

    char line[8192];
    if (!fgets(line, sizeof line, f)) { fprintf(stderr, "empty file\n"); return 2; }

    int rows = 0, bad = 0;
    double worst = 0.0;
    while (fgets(line, sizeof line, f)) {
        line[strcspn(line, "\r\n")] = '\0';
        if (line[0] == '\0') continue;
        char *fld[8];
        int n = split(line, fld, 8);
        if (n != 5) {
            printf("  row %d: expected 5 fields, found %d\n", rows + 1, n);
            bad++; rows++; continue;
        }
        char *id = fld[0], *expr = fld[1];
        unescape(expr);
        double claimed = strtod(fld[2], NULL);
        const char *want_status = fld[3];
        const char *want_value = fld[4];

        double value = 0.0;
        const char *got = verdict(expr, claimed, &value);
        rows++;

        if (strcmp(got, want_status) != 0) {
            printf("  %s (%s): python says %s, C says %s\n",
                   id, expr, want_status, got);
            bad++;
            continue;
        }
        if (want_value[0] != '\0') {
            double want = strtod(want_value, NULL);
            double denom = fabs(want) > 1.0 ? fabs(want) : 1.0;
            double diff = fabs(value - want) / denom;
            if (diff > worst) worst = diff;
            if (diff > VALUE_TOL) {
                printf("  %s (%s): python computed %.17g, C computed %.17g\n",
                       id, expr, want, value);
                bad++;
            }
        } else if (strcmp(got, "unverifiable") != 0) {
            printf("  %s: no reference value but status is %s\n", id, got);
            bad++;
        }
    }
    fclose(f);

    if (rows == 0) { printf("no cases read from %s\n", path); return 1; }
    if (bad) {
        printf("C disagrees with Python on %d of %d check_math cases\n", bad, rows);
        return 1;
    }
    printf("C reproduces all %d check_math verdicts, "
           "worst value disagreement %.1e relative\n", rows, worst);
    return 0;
}
