/*
 * lm_stubs.c — bare-metal scalar math implementations (medany, no libm dep)
 *
 * Drop-in replacement for libm when linking at 0x80000000 (medlow broken).
 * Simple but functional for throughput comparison.
 */

/* avoid newlib math.h macro expansions that confuse the parser */
#define _WANT_MATH_ERRNO 0
#define _WANT_IO_C99_FORMATS 0

/*
 * math.h is included by veclibm.c; we just provide the symbols.
 * Declare only what we implement to avoid macro conflicts.
 */
double sqrt(double);
double cbrt(double);
double exp(double);
double exp2(double);
double expm1(double);
double log(double);
double log10(double);
double log2(double);
double log1p(double);
double sin(double);
double cos(double);
double tan(double);
double asin(double);
double acos(double);
double atan(double);
double sinh(double);
double cosh(double);
double tanh(double);
double asinh(double);
double acosh(double);
double atanh(double);
double erf(double);
double erfc(double);
double tgamma(double);
double lgamma(double);

/* --- helpers --- */
static double fabs_(double x) { return x < 0 ? -x : x; }
static double copysign_(double x, double y) {
	union { double d; unsigned long long u; } ux = {x}, uy = {y};
	ux.u = (ux.u & 0x7fffffffffffffffULL) | (uy.u & 0x8000000000000000ULL);
	return ux.d;
}

static const double M_E    = 2.71828182845904523536;
static const double M_LN2  = 0.69314718055994530942;
static const double M_LN10 = 2.30258509299404568402;
static const double M_PI   = 3.14159265358979323846;
static const double M_PI_2 = 1.57079632679489661923;

/* --- sqrt (hardware) --- */
double sqrt(double x) { return __builtin_sqrt(x); }

/* --- cbrt (Newton) --- */
double cbrt(double x) {
	double s = x < 0 ? -1.0 : 1.0;
	x = fabs_(x);
	double y = x < 1.0 ? x : 1.0;
	for (int i = 0; i < 10; i++)
		y = (2.0*y + x/(y*y)) / 3.0;
	return s * y;
}

/* --- exp (series) --- */
double exp(double x) {
	if (x > 700) return 1e300;
	if (x < -700) return 0;
	double r = 1.0, t = 1.0;
	for (int i = 1; i < 30; i++) { t *= x / i; r += t; }
	return r;
}

double exp2(double x) { return exp(x * M_LN2); }

double expm1(double x) {
	if (fabs_(x) < 1e-5) return x * (1.0 + x * 0.5);
	return exp(x) - 1.0;
}

/* --- log (reduction + series) --- */
double log(double x) {
	if (x <= 0) return -1.0/0.0;
	int e = 0;
	while (x > M_E)   { x /= M_E;   e++; }
	while (x < 1.0/M_E) { x *= M_E; e--; }
	x -= 1.0;
	double r = x, t = x, s = x;
	for (int i = 2; i < 20; i++) { t *= -x; s = t / i; r += s; }
	return r + e;
}

double log10(double x) { return log(x) / M_LN10; }
double log2(double x)  { return log(x) / M_LN2;  }
double log1p(double x) { return log(1.0 + x); }

/* --- sin/cos (range reduction + Taylor) --- */
static double sin_kernel(double x) {
	double x2 = x*x, r = x, t = x;
	for (int i = 3; i < 20; i += 2) { t *= -x2 / (i*(i-1)); r += t; }
	return r;
}

static double cos_kernel(double x) {
	double x2 = x*x, r = 1.0, t = 1.0;
	for (int i = 2; i < 20; i += 2) { t *= -x2 / (i*(i-1)); r += t; }
	return r;
}

static double reduce(double x) {
	/* manual range reduction to [0, 2*PI) */
	double two_pi = 2.0 * M_PI;
	if (x < 0) { double n = (long long)(x / two_pi) - 1; x -= n * two_pi; }
	else      { double n = (long long)(x / two_pi);     x -= n * two_pi; }
	return x;
}

double sin(double x) {
	x = reduce(x);
	if (x > M_PI) return -sin_kernel(x - M_PI);
	return sin_kernel(x);
}

double cos(double x) { return sin(x + M_PI_2); }

double tan(double x) {
	double s = sin(x), c = cos(x);
	return s / c;
}

/* --- asin --- */
double asin(double x) {
	if (fabs_(x) >= 1.0) return copysign_(M_PI_2, x);
	double r = x, t = x, x2 = x*x;
	for (int i = 3; i < 30; i += 2) { t *= x2 * (i-2) / (i-1); r += t / i; }
	return r;
}

double acos(double x) { return M_PI_2 - asin(x); }

double atan(double x) {
	int inv = fabs_(x) > 1.0;
	if (inv) x = 1.0 / x;
	double x2 = x*x, r = x, t = x;
	for (int i = 3; i < 40; i += 2) { t *= -x2; r += t / i; }
	if (inv) r = M_PI_2 - r;
	return r;
}

/* --- sinh/cosh/tanh --- */
double sinh(double x) { double e = exp(x); return 0.5*(e - 1.0/e); }
double cosh(double x) { double e = exp(x); return 0.5*(e + 1.0/e); }
double tanh(double x) { double s = sinh(x), c = cosh(x); return s/c; }

double asinh(double x) { return log(x + sqrt(x*x + 1.0)); }
double acosh(double x) { return log(x + sqrt(x*x - 1.0)); }
double atanh(double x) { return 0.5 * log((1.0+x)/(1.0-x)); }

/* --- erf/erfc (approximation) --- */
double erf(double x) {
	int s = x < 0 ? -1 : 1; x = fabs_(x);
	double t = 1.0 / (1.0 + 0.3275911*x);
	double r = 1.0 - (((((1.061405429*t - 1.453152027)*t) + 1.421413741)*t
	                  - 0.284496736)*t + 0.254829592)*t * exp(-x*x);
	return s * r;
}

double erfc(double x) { return 1.0 - erf(x); }

/* --- tgamma/lgamma (Stirling) --- */
double tgamma(double x) {
	if (x < 0.5) return M_PI / (sin(M_PI*x) * tgamma(1.0 - x));
	x -= 1.0;
	double r = sqrt(2.0*M_PI/x) * exp(x * log(x/M_E) + 1.0/(12.0*x));
	return r;
}

double lgamma(double x) {
	if (x <= 0) return 1.0/0.0;
	return log(tgamma(x));
}
