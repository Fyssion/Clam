import sqlite3


def create_connection(db_file):
    """ create a database connection to the SQLite database
        specified by db_file
    :param db_file: database file
    :return: Connection object or None
    """
    conn = None
    try:
        conn = sqlite3.connect(db_file)
        return conn
    except sqlite3.Error as e:
        print(e)

    return conn


def create_table(conn, sql_code):
    """ create a table from the sql_code statement
    :param conn: Connection object
    :param sql_code: a CREATE TABLE statement
    :return:
    """
    try:
        c = conn.cursor()
        c.execute(sql_code)
    except sqlite3.Error as e:
        print(e)


def create_project(conn, project):
    """
    Create a new project into the projects table
    :param conn:
    :param project:
    :return: project id
    """
    sql = ''' INSERT INTO projects(name,begin_date,end_date)
              VALUES(?,?,?) '''
    cur = conn.cursor()
    cur.execute(sql, project)
    return cur.lastrowid


def create_task(conn, task):
    """
    Create a new task
    :param conn:
    :param task:
    :return:
    """

    sql = ''' INSERT INTO tasks(name,priority,status_id,project_id,begin_date,end_date)
              VALUES(?,?,?,?,?,?) '''
    cur = conn.cursor()
    cur.execute(sql, task)
    return cur.lastrowid


def projects_and_tasks(database):

    projects_table = """ CREATE TABLE IF NOT EXISTS projects (
                                        id integer PRIMARY KEY,
                                        name text NOT NULL,
                                        begin_date text,
                                        end_date text
                                    ); """

    tasks_table = """CREATE TABLE IF NOT EXISTS tasks (
                                    id integer PRIMARY KEY,
                                    name text NOT NULL,
                                    priority integer,
                                    status_id integer NOT NULL,
                                    project_id integer NOT NULL,
                                    begin_date text NOT NULL,
                                    end_date text NOT NULL,
                                    FOREIGN KEY (project_id) REFERENCES projects (id));"""

    # create a database connection
    conn = create_connection(database)

    # create tables
    if conn is not None:
        # create projects table
        create_table(conn, projects_table)

        # create tasks table
        create_table(conn, tasks_table)
    else:
        print("Error! cannot create the database connection.")


def add_data_to_projects(database):
    # create a database connection
    conn = create_connection(database)
    with conn:
        # create a new project
        project = ('Cool App with SQLite & Python', '2020-01-01', '2020-01-30')
        project_id = create_project(conn, project)

        # tasks
        task_1 = ('Analyze the requirements of the app', 1, 1, project_id,
                  '2020-01-01', '2020-01-02')
        task_2 = ('Confirm with user about the top requirements', 1, 1,
                  project_id, '2020-01-03', '2020-01-05')

        # create tasks
        create_task(conn, task_1)
        create_task(conn, task_2)


def create_prefix(conn, prefix):
    """
    Create a new project into the projects table
    :param conn:
    :param project:
    :return: project id
    """
    sql = ''' INSERT INTO projects(name,begin_date,end_date)
              VALUES(?,?,?) '''
    cur = conn.cursor()
    cur.execute(sql, prefix)
    return cur.lastrowid


def prefixes_table(database):
    prefixes_table = """CREATE TABLE IF NOT EXISTS prefixes (
                                        id integer PRIMARY KEY,
                                        prefixes text
                                    ); """

    conn = create_connection(database)

    # create tables
    if conn is not None:
        # create projects table
        create_table(conn, prefixes_table)
    else:
        print("Error! cannot create the database connection.")


def add_data_to_prefixes(database, sql):
    conn = create_connection(database)
    with conn:
        # create a new project
        guild = ("")
        create_prefix(conn, guild)


if __name__ == '__main__':
    database = "database.db"
    # projects_and_tasks(database)
    add_data_to_projects(database)
