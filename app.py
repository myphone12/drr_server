from flask import Flask,request
import datetime,json
app = Flask(__name__)
song_num = 132
with open("./save.json","r") as f:
    save = json.loads(f.read())
@app.route("/DanceRail/<data>")
def hello(data:str):

    global song_num,save

    if data.startswith("usecard.php"): #input:cardid, return:number of coin
        d = request.args.to_dict()
        try:
            id = int(d["cardid"])
            return str(id)
        except:
            return "10"
    
    if data.startswith("date.php"): #input:None, return:current date (eg:20251021(means 2025year October 21st))
        ct = datetime.datetime.now()
        return f"{ct.year}{ct.month}{ct.day}"
    
    if data.startswith("signup.php"): #input:password(uid),allscore,exp,nickname,onlineid,subtitle
                                      #return:0(means OK)
        d = request.args.to_dict()
        try:
            with open("./data.json","r") as f:
                data = json.loads(f.read())
                try:
                    for i in range(len(data)):
                        if data[i]["password"] == d["password"]:
                            for k in d.keys():
                                data[i][k] = d[k]
                except:
                        data.append(d)
            with open("./data.json","w") as f:
                f.write(json.dumps(data, sort_keys=True, indent=4))
        except:
            with open("./data.json","w") as f:
                f.write(json.dumps([d,], sort_keys=True, indent=4))
        return "0"

    if data.startswith("update.php"): #input:password(uid),allscore,exp,nickname,onlineid,subtitle
                                      #return:0(means OK)
        d = request.args.to_dict()
        try:
            with open("./data.json","r") as f:
                data = json.loads(f.read())
                try:
                    for i in range(len(data)):
                        if data[i]["password"] == d["password"]:
                            for k in d.keys():
                                data[i][k] = d[k]
                except:
                        data.append(d)
            with open("./data.json","w") as f:
                f.write(json.dumps(data, sort_keys=True, indent=4))
        except:
            with open("./data.json","w") as f:
                f.write(json.dumps([d,], sort_keys=True, indent=4))
        return "0"
    
    if data.startswith("cheat_test.php"): #input:None,return:0/1(no cheat/cheat)
        return "0"
    
    if data.startswith("gethit.php"): #input:songnum,return:todo
        d = request.args.to_dict()
        song_num = int(d["song_num"])
        return ">1>1>2>2>3>3>4>4"
    
    if data.startswith("getstageclear.php"):
        s = ""
        for i in range(song_num):
            s += ">" + str((i*10)%100)
        return s

    if data.startswith("gethiexpnew.php"): #input:None,return:">player name>rating>exp"
        s = ""
        for i in range(10):
            s += ">Name" + str(i) + ">" + str(10.00-i) + ">" + str((10-i)**2)
        return s
    
    if data.startswith("gethiscorenew.php"): #input:None,return:">player name>rating>score"
        s = ""
        for i in range(10):
            s += ">Name" + str(i) + ">" + str(10.00-i) + ">" + str((10-i)*500)
        return s
    
    if data.startswith("gethiratingnew.php"): #input:None,return:">player name>exp>rating"
        s = ""
        for i in range(10):
            s += ">Name" + str(i) + ">" + str((10-i)**2) + ">" + str(10.00-i)
        return s
    
    if data.startswith("uploadsavedata.php"): #input:player(download id),dataname,data,return:0(means OK)
        d = request.args.to_dict()
        try:
            save[d["player"]].keys()
        except:
            save[d["player"]] = {}
        save[d["player"]][d["dataname"]]=d["data"]
        if len(save[d["player"]].keys()) == 89+song_num*24:
            with open("./save.json","w") as f:
                f.write(json.dumps(save, sort_keys=True, indent=4))
        return "1"
    
    if data.startswith("getsavedata.php"): #input:player(download id),dataname,return:data
        d = request.args.to_dict()
        return str(save[d["player"]][d["dataname"]])
    
    if data.startswith("getsavedatanum.php"): #input:player(download id),return:number of data
        d = request.args.to_dict()
        return str(len(save[d["player"]]))



if __name__ == "__main__":
    app.run(host='0.0.0.0', port=80) #run server