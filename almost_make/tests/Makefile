# To be run with AlmostMake.
include *.mk

ALL := testSimple testPhony testMacros testRecursion testParallel testMisc testModTime \
		testCannedRecipes
all: $(ALL)

ifeq ($(MAKE_VERSION),)
  FLAGS = --undefined-is-error
else
  FLAGS = --warn-undefined-variables
endif

# Perhaps not legal in other versions of make, but it
# should work here. As such, it is tested.
$(ALL):
	$(MAKE) $(FLAGS) -C $@ clean
	$(MAKE) $(FLAGS) -C $@ check
	$(MAKE) $(FLAGS) -C $@ clean

.PHONY: all $(ALL)
