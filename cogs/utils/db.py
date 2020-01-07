import json

class Profile:
    def __init__(self, id):
        self.id = id

    def save(self):
        add_profile(self)
    def delete(self):
        delete_profile(self)

class Guild(Profile):
    def __init__(self, id):
        self.id = id

database_file = "database.json"

def profile_to_data(profile, data):
    data[str(profile.id)] = {
        "name": profile.name,
        "timezone": profile.timezone
    }
    if profile.last_conversation:
        data[str(profile.id)]["last_conversation"] = profile.last_conversation

    return data

def data_to_profile(data, id):
    if str(id) in data:
        saved_usr = data[str(id)]
        user = Profile(id)
        user.name = saved_usr["name"]
        user.timezone = saved_usr["timezone"]
        if saved_usr["last_conversation"]:
            user.last_conversation = saved_usr["last_conversation"]
        return user

    else:
        return None


def add_profile(profile):
    with open(database_file, 'r') as f:
        data = json.load(f)

    data = profile_to_data(profile, data)

    with open(database_file, 'w') as f:
        json.dump(data, f)

update_profile = add_profile


# def update_profile(profile):
#     with open(database_file, 'r') as f:
#         data = json.load(f)

#     if str(id) in data:
        

#         with open(database_file, 'w') as f:
#             json.dump(data, f)

#     else:
#         add_profile(profile)


def get_profile(id):
    with open(database_file, 'r') as f:
        data = json.load(f)

    return data_to_profile(data, id)


def delete_profile(id):
    with open(database_file, 'r') as f:
        data = json.load(f)

    if get_profile(id):
        del data[str(id)]
        with open(database_file, 'w') as f:
            json.dump(data, f)