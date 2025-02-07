import datetime as dt
from shutil import copyfileobj
import subprocess

from wmfdata.utils import print_err, mediawiki_dt
from wmfdata import spark

def run(cmds, fmt = "pandas", spark_master='yarn', app_name='wmfdata', spark_config={}):
    """
    Run one or more Hive queries or command on the Data Lake.

    If multiple commands are provided, only results from the last results-producing command will be returned.
    """

    if fmt not in ["pandas", "raw"]:
        raise ValueError("The `fmt` should be either `pandas` or `raw`.")
    if type(cmds) == str:
        cmds = [cmds]

    spark_session = spark.get_session(spark_master, app_name, spark_config)

    result_DF = None
    for cmd in cmds:
        cmd_result = spark_session.sql(cmd)
        
        # If the result has columns, the command was a query and therefore results-producing.
        # If not, it was a DDL or DML command and not results-producing.
        if len(cmd_result.columns) > 0:
            result_DF = cmd_result
    if fmt == 'pandas':
        result = result_DF.toPandas()
    else:
        result = result_DF.collect()
    
    spark.start_session_timeout(spark_session)
    return result

def load_csv(
    path, field_spec, db_name, table_name,
    create_db=False, sep=",", headers=True
):
    """
    Upload a CSV (or other delimiter-separated value file) to Data Lake's HDFS, for use with Hive
    and other utilities.

    `field_spec` specifies the field names and their formats, for the
    `CREATE TABLE` statement; for example, `name string, age int, graduated bool`.

    To prevent errors caused by typos, the function will not try to create the database first unless
    `create_db=True` is passed.

    `headers` gives whether the file has a header row; if it does, the function strips it before
    uploading, because Hive treats all rows as data rows.
    """
    if headers:
        new_path = "/tmp/wmfdata-" + mediawiki_dt(dt.datetime.now())
        # From rbtsbg at https://stackoverflow.com/a/39791546
        with open(path, 'r') as source, open(new_path, 'w') as target:
            source.readline()
            copyfileobj(source, target)

        path = new_path

    create_db_cmd = """
    create database if not exists {db_name}
    """.format(
        db_name=db_name
    )

    # To do: Passing a new field spec cannot change an exsting table's format
    create_table_cmd = """
    create table if not exists {db_name}.{table_name} ({field_spec})
    row format delimited fields terminated by "{sep}"
    """.format(
        db_name=db_name, table_name=table_name,
        field_spec=field_spec, sep=sep
    )

    load_table_cmd = """
        load data local inpath "{path}"
        overwrite into table {db_name}.{table_name}
    """.format(
        # To do: Convert relative paths (e.g. "~/data.csv") into absolute paths
        path=path, db_name=db_name,
        table_name=table_name
    )

    if create_db:
        run(create_db_cmd)

    run(create_table_cmd)

    proc = subprocess.Popen(
        ["hive", "-e", load_table_cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT
    )

    try:
        outs, _ = proc.communicate(timeout=15)
        for line in outs.decode().split("\n"):
            print_err(line)
    except TimeoutExpired:
        proc.kill()
        outs, _ = proc.communicate()
        for line in outs.decode().split("\n"):
            print_err(line)
