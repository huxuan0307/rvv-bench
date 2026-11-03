#include "../../nolibc.h"
#include "p1_cases.h"

#ifndef P1_RUNS
#define P1_RUNS 3
#endif

extern unsigned long p1_mem[];
extern unsigned long p1_actual_vl;
extern unsigned long p1_active;
#define CASE_ROW(id,kind,op,lmul,pattern,status,skip,sew,avl,ieew,chains,insns,active,run,check) \
	{ id,kind,op,lmul,pattern,status,skip,sew,avl,ieew,chains,insns,active,run,check }
static struct p1_case cases[] = { P1_CASES };
#undef CASE_ROW

static unsigned char buffer[65536] __attribute__((aligned(64)));

static void
csv(const char *s)
{
	print("\"");
	for (; *s; ++s) {
		if (*s == '"') print("\"\"");
		else print_raw(s, 1);
	}
	print("\"");
}

static unsigned long
run_case(struct p1_case *c)
{
	return c->run();
}

int
main(void)
{
	for (unsigned i = 0; i < sizeof buffer; ++i)
		buffer[i] = (unsigned char)(33 + (i & 31));
	/* Generated assembly currently uses a fixed local p1_mem symbol. */
	(void)p1_mem;
	print("case_id,kind,opcode,sew,data_eew,lmul,avl,actual_vl,index_eew,mask_pattern,chains,active,target_insns,sample,cycles,status,check_status,skip_reason\n");
	for (unsigned i = 0; i < sizeof cases / sizeof cases[0]; ++i) {
		struct p1_case *c = &cases[i];
		unsigned samples = c->samples;
		for (unsigned sample = 0; sample < samples; ++sample) {
			unsigned long cycles = 0;
			unsigned check = 0;
			const char *status = c->status;
			if (status[0] != 's') {
				cycles = run_case(c);
				check = c->check();
				status = check ? "fail" : "pass";
			}
			csv(c->id); print(","); csv(c->kind); print(","); csv(c->opcode);
			print(",")(u,c->sew)(",")(u,c->data_eew)(","); csv(c->lmul);
			print(",")(u,c->avl)(",")(u,p1_actual_vl)(",")(u,c->index_eew)(",");
			csv(c->pattern);
			print(",")(u,c->chains)(",")(u,(c->kind[0] == 's' ? p1_active : c->active))(",")(u,c->target_insns)(",")(u,sample);
			print(",")(u,cycles)(","); csv(status); print(",");
			csv(check ? "not_checked_or_fail" : (status[0] == 's' ? "skip" : "pass")); print(","); csv(c->skip);
			print("\n");
			print_flush();
		}
	}
	print("P1_COMPLETE\n");
	print_flush();
	return 0;
}
