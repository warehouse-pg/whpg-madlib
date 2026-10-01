#!/usr/bin/python3
# ------------------------------------------------------------------------------
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
# ------------------------------------------------------------------------------

# Create changelist for any two branches/tags

# Prequisites:
# The old version has to be installed in the "madlib_old_vers" schema
# The new version has to be installed in the "madlib" (default) schema
# Two branches/tags must exist locally (run 'git fetch' to ensure you have the latest version)
# The current branch does not matter

# Usage (must be executed in the src/madpack directory):
# python create_changelist.py <database name> <old version branch> <new version branch> <changelist filename>
# If you are using the master branch, please make sure to edit the branch/tag in the output file

# Example (should be equivalent to changelist_1.13_1.14.yaml):
# python create_changelist.py madlib rel/v1.13 rel/v1.14 chtest1.yaml

import sys
import os
import shutil
import subprocess
import tempfile

database = sys.argv[1]
old_vers = sys.argv[2]
new_vers = sys.argv[3]
ch_filename = sys.argv[4]

for vers in (old_vers, new_vers):
    if vers.startswith('-'):
        print("Version branch/tag '{0}' must not start with '-'".format(vers))
        raise SystemExit


def run(args, out_path=None):
    """Run a command without a shell, sending stdout to out_path or discarding it."""
    if out_path is None:
        return subprocess.call(args, stdout=subprocess.DEVNULL)
    with open(out_path, 'w') as out:
        return subprocess.call(args, stdout=out)


def psql(*args, out_path=None):
    return run(['psql', '-d', database] + list(args), out_path)


if os.path.exists(ch_filename):
    print("{0} already exists".format(ch_filename))
    raise SystemExit

err1 = psql('-l')
if err1 != 0:
    print("Database {0} does not exist".format(database))
    raise SystemExit

err1 = psql('-c', 'select madlib_old_vers.version()')
if err1 != 0:
    print("MADlib is not installed in the madlib_old_vers schema. Please refer to the Prequisites.")
    raise SystemExit

err1 = psql('-c', 'select madlib.version()')
if err1 != 0:
    print("MADlib is not installed in the madlib schema. Please refer to the Prequisites.")
    raise SystemExit

print("Creating changelist {0}".format(ch_filename))
tmp_dir = tempfile.mkdtemp(prefix='madlib_changelist_')


def tmp(name):
    return os.path.join(tmp_dir, name)


try:
    # Find the new modules using the git diff
    err1 = run(['git', 'diff', old_vers, new_vers, '--name-only', '--diff-filter=A'],
               tmp('nm.txt'))
    if err1 != 0:
        print("Git diff failed. Please ensure that branches/tags are fetched.")
        raise SystemExit

    f = open(tmp('cl.yaml'), "w")
    f.write(
"""# ------------------------------------------------------------------------------
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
# ------------------------------------------------------------------------------
""")

    f.write(
    """
# Changelist for MADlib version {old_vers} to {new_vers}

# This file contains all changes that were introduced in a new version of
# MADlib. This changelist is used by the upgrade script to detect what objects
# should be upgraded (while retaining all other objects from the previous version)

# New modules (actually .sql_in files) added in upgrade version
# For these files the sql_in code is retained as is with the functions in the
# file installed on the upgrade version. All other files (that don't have
# updates), are cleaned up to remove object replacements
""".format(**locals()))

    # Find the new .sql_in files that are not in test folders
    f.write("new module:\n")
    with open(tmp('nm.txt')) as fp:
        for line in fp:
            if 'sql_in' in line and '/test/' not in line:
                 f.write('    ' + line.split('/')[5].split('.')[0]+':\n')

    # Find the changed types and keep a list for future use
    psql('-f', 'diff_udt.sql', out_path=tmp('udt.txt'))

    f.write("\n# Changes in the types (UDT) including removal and modification\n")
    f.write("udt:\n")
    udt_list=[]
    with open(tmp('udt.txt')) as fp:
        for line in fp:

            # Both the type and its array form shows up as separate types
            # We don't need both of them so we remove the array
            if 'UDT' in line and '[]' not in line:
                ch_type = line.split('|')[1].strip()
                udt_list.append(ch_type)
                f.write('    ' + ch_type +":\n")

    # Find the list of UDFs and UDAs
    # There are two main sources for these lists.
    # 1. The functions that actually got changed
    # 2. The functions that depend on a changed type

    # We will keep two lists (for UDF and UDA) and fill them as we parse the
    # output of diff functions

    udf_list=[]
    uda_list=[]
    current_list = udf_list

    # Find the changed functions/aggregates via the diff_udf script and write them to a file
    psql('-f', 'diff_udf.sql', out_path=tmp('udf.txt'))

    # The entries in the file are ordered by type.
    # Read them line by line and add to the udf list until the first aggregate
    with open(tmp('udf.txt')) as fp:
        for line in fp:
            if 'type' in line:
                # When we get the first aggregate, we switch the current list
                if 'agg' in line:
                    current_list = uda_list
            if 'UDF' in line:
                current_list.append(line.split('|')[1])

    current_list = udf_list

    # Find the function that return a changed type
    # Note that we already ran the diff_udf.sql file
    # This means the get_functions() function is already defined in the database
    for t in udt_list:

        psql('-c', 'DROP TABLE IF EXISTS __tmp__madlib__ ')

        # Find all of the old functions that return this particular type t and write to a table
        psql('-c', "SELECT get_functions('__tmp__madlib__', 'madlib_old_vers', 'madlib_old_vers.{t}')".format(**locals()))

        # Order them in descending order on type so that we read the functions first and aggregates last
        psql('-x', '-c', 'SELECT type, name, retype, argtypes FROM __tmp__madlib__ ORDER BY type DESC',
             out_path=tmp('typedep.txt'))

        psql('-c', 'DROP TABLE IF EXISTS __tmp__madlib__ ')

        with open(tmp('typedep.txt')) as fp:
            for line in fp:
                if '|' in line:
                    sp = line.split('|')

                    # Type is only used for switching the current list from udf to uda
                    if sp[0].strip() == 'type':
                        if sp[1].strip() == 'agg':
                            current_list = uda_list

                    # Add the functions with formatting
                    elif sp[0].strip() == 'name':
                        current_list.append('    - ' + sp[1].strip() + ":\n")
                    elif sp[0].strip() == 'retype':
                        current_list.append('        rettype: schema_madlib.' + sp[1].strip() + "\n")
                    elif sp[0].strip() == 'argtypes':
                        current_list.append('        argument: ' + sp[1].strip() + "\n")

    # Write the UDF and UDA lists to the changelist
    f.write(
    """
# List of the UDF changes that affect the user externally. This includes change
# in function name, return type, argument order or types, or removal of
# the function. In each case, the original function is as good as removed and a
# new function is created. In such cases, we should abort the upgrade if there
# are user views dependent on this function, since the original function will
# not be present in the upgraded version.
""")

    f.write("udf:\n")
    for line in udf_list:
        f.write(line)

    f.write("""
# Changes to aggregates (UDA) including removal and modification
# Overloaded functions should be mentioned separately
""")

    f.write("uda:\n")
    for line in uda_list:
        f.write(line)

    #TODO: We need to decide how we want to check for these
    f.write(
    """
# List of the UDC, UDO and UDOC changes.
""")
    f.write("udc:\n")

    # Find the changed operators

    f.write("\n# Changes in the operators (UDO)\n")
    f.write("udo:\n")

    psql('-f', 'diff_udo.sql', out_path=tmp('udo.txt'))
    with open(tmp('udo.txt')) as fp:
        for line in fp:
            if ' | ' in line:
                    sp = line.split(' | ')

                    if sp[0].strip() == 'name':
                        f.write("    - '" + sp[1].strip() + "':\n")
                    elif sp[0].strip() == 'rettype':
                        f.write('        rettype: ' + sp[1].strip() + "\n")
                    elif sp[0].strip() == 'oprright':
                        f.write('        rightarg: ' + sp[1].strip() + "\n")
                    elif sp[0].strip() == 'oprleft':
                        f.write('        leftarg: ' + sp[1].strip() + "\n")

    for t in udt_list:

        psql('-c', 'DROP TABLE IF EXISTS __tmp__madlib__ ')

        psql('-c', "SELECT get_udos('__tmp__madlib__', 'madlib_old_vers', '{t}')".format(**locals()))

        psql('-x', '-c', 'SELECT name, rettype, oprright, oprleft FROM __tmp__madlib__ ORDER BY name DESC',
             out_path=tmp('typedep_udo.txt'))

        psql('-c', 'DROP TABLE IF EXISTS __tmp__madlib__ ')

        with open(tmp('typedep_udo.txt')) as fp:
            for line in fp:
                if '|' in line:
                    sp = line.split('|')

                    if sp[0].strip() == 'name':
                        f.write("    - '" + sp[1].strip() + "':\n")
                    elif sp[0].strip() == 'rettype':
                        f.write('        rettype: ' + sp[1].strip() + "\n")
                    elif sp[0].strip() == 'oprright':
                        f.write('        rightarg: ' + sp[1].strip() + "\n")
                    elif sp[0].strip() == 'oprleft':
                        f.write('        leftarg: ' + sp[1].strip() + "\n")


    # Find the changed operator classes

    f.write("\n# Changes in the operator classes (UDOC)\n")
    psql('-f', 'diff_udoc.sql', out_path=tmp('udoc.txt'))
    f.write("udoc:\n")
    with open(tmp('udoc.txt')) as fp:
        for line in fp:
            if '|' in line:
                    sp = line.split('|')

                    if sp[0].strip() == 'opfamily_name':
                        f.write("    - '" + sp[1].strip() + "':\n")
                    elif sp[0].strip() == 'index_method':
                        f.write('        index_method: ' + sp[1].strip() + "\n")

    for t in udt_list:

        psql('-c', 'DROP TABLE IF EXISTS __tmp__madlib__ ')

        psql('-c', "SELECT get_udocs('__tmp__madlib__', 'madlib_old_vers', '{t}')".format(**locals()))

        psql('-x', '-c', 'SELECT opfamily_name, index_method FROM __tmp__madlib__ ORDER BY opfamily_name DESC',
             out_path=tmp('typedep_udoc.txt'))

        psql('-c', 'DROP TABLE IF EXISTS __tmp__madlib__ ')

        with open(tmp('typedep_udoc.txt')) as fp:
            for line in fp:
                if '|' in line:
                    sp = line.split('|')

                    if sp[0].strip() == 'opfamily_name':
                        f.write("    - " + sp[1].strip() + ":\n")
                    elif sp[0].strip() == 'index':
                        f.write('        index: ' + sp[1].strip() + "\n")

    f.close()

    # Copy the new changelist file to its proper location
    # This helps to keep the madlib folder clean in case the program stops
    # unexpectedly
    shutil.copyfile(tmp('cl.yaml'), ch_filename)

except:
    print("Something went wrong! The changelist might be wrong/corrupted.")
    raise
finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)
